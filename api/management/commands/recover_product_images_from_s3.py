from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable

import boto3
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import Product, ProductImage


UUID_PREFIX_RE = re.compile(r"^[0-9a-f]{32}-", re.I)
EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp)$", re.I)
GENERIC_TOKENS = {
    "image",
    "images",
    "result",
    "product",
    "small",
    "large",
    "medium",
    "set",
    "door",
    "doors",
    "drawer",
    "drawers",
    "with",
    "and",
    "the",
    "of",
}


@dataclass
class Candidate:
    url: str
    key: str
    filename_slug: str
    tokens: set[str]


class Command(BaseCommand):
    help = (
        "Recover missing ProductImage rows by scanning the configured S3 media bucket "
        "and matching object filenames to product slugs/names. Defaults to dry-run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write ProductImage rows. Without this flag the command only reports matches.",
        )
        parser.add_argument(
            "--replace-existing",
            action="store_true",
            help="Replace existing images for matched products instead of only filling missing ones.",
        )
        parser.add_argument(
            "--product-id",
            type=int,
            action="append",
            dest="product_ids",
            default=[],
            help="Restrict recovery to specific product IDs. Can be supplied multiple times.",
        )
        parser.add_argument(
            "--subcategory",
            action="append",
            dest="subcategories",
            default=[],
            help="Restrict recovery to subcategory slugs. Can be supplied multiple times.",
        )
        parser.add_argument(
            "--max-images",
            type=int,
            default=4,
            help="Maximum number of matched images to keep per product.",
        )
        parser.add_argument(
            "--min-score",
            type=int,
            default=3,
            help="Minimum score required before a bucket object is considered a match.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Only process the first N products after filtering. 0 means no limit.",
        )

    def handle(self, *args, **options):
        bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", "").strip()
        region = getattr(settings, "AWS_S3_REGION_NAME", "").strip()
        access_key = getattr(settings, "AWS_ACCESS_KEY_ID", None)
        secret_key = getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
        media_url = getattr(settings, "MEDIA_URL", "").rstrip("/") + "/"

        if not bucket or not region or not access_key or not secret_key:
            raise CommandError("AWS S3 settings are incomplete. Check backend/.env and settings.")

        client = boto3.client(
            "s3",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        candidates = self._load_candidates(client, bucket, media_url)
        if not candidates:
            raise CommandError("No image candidates were found in S3 under the media/ prefix.")

        products = Product.objects.select_related("category", "subcategory").all().order_by("id")
        if options["product_ids"]:
            products = products.filter(id__in=options["product_ids"])
        if options["subcategories"]:
            products = products.filter(subcategory__slug__in=options["subcategories"])
        if not options["replace_existing"]:
            products = products.filter(images__isnull=True).distinct()
        if options["limit"]:
            products = products[: options["limit"]]

        apply_changes = bool(options["apply"])
        replace_existing = bool(options["replace_existing"])
        max_images = max(1, options["max_images"])
        min_score = max(1, options["min_score"])

        total_products = 0
        matched_products = 0
        restored_rows = 0

        for product in products:
            total_products += 1
            matches = self._match_candidates(product, candidates, min_score=min_score, max_images=max_images)
            if not matches:
                self.stdout.write(self.style.WARNING(f"[MISS] {product.id} {product.name}"))
                continue

            matched_products += 1
            self.stdout.write(self.style.SUCCESS(f"[MATCH] {product.id} {product.name}"))
            for score, candidate in matches:
                self.stdout.write(f"  score={score:02d} {candidate.key}")

            if not apply_changes:
                continue

            with transaction.atomic():
                if replace_existing:
                    product.images.all().delete()

                for _, candidate in matches:
                    ProductImage.objects.create(
                        product=product,
                        url=candidate.url,
                        alt_text=self._default_alt_text(product.name, candidate),
                    )
                    restored_rows += 1

        mode = "APPLY" if apply_changes else "DRY-RUN"
        self.stdout.write("")
        self.stdout.write(
            self.style.NOTICE(
                f"{mode} complete. scanned_products={total_products} matched_products={matched_products} restored_rows={restored_rows}"
            )
        )

    def _load_candidates(self, client, bucket: str, media_url: str) -> list[Candidate]:
        paginator = client.get_paginator("list_objects_v2")
        results: list[Candidate] = []

        for page in paginator.paginate(Bucket=bucket, Prefix="media/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                lowered = key.lower()
                if not lowered.endswith((".jpg", ".jpeg", ".png", ".webp")):
                    continue
                filename = key.rsplit("/", 1)[-1]
                filename = UUID_PREFIX_RE.sub("", filename)
                filename_slug = EXT_RE.sub("", filename)
                filename_slug = filename_slug.replace("_result_result", "").replace("_result", "")
                tokens = self._tokenize(filename_slug)
                if not tokens:
                    continue

                # ProductImage.url stores the full public URL in this project.
                relative_key = key.split("media/", 1)[1] if "media/" in key else key
                results.append(
                    Candidate(
                        key=key,
                        url=f"{media_url}{relative_key}",
                        filename_slug=filename_slug,
                        tokens=tokens,
                    )
                )

        return results

    def _match_candidates(
        self,
        product: Product,
        candidates: Iterable[Candidate],
        *,
        min_score: int,
        max_images: int,
    ) -> list[tuple[int, Candidate]]:
        product_tokens = self._product_tokens(product)
        slug_text = "-".join(sorted(product_tokens))
        scored: list[tuple[int, Candidate]] = []

        for candidate in candidates:
            overlap = len(product_tokens & candidate.tokens)
            if overlap == 0:
                continue

            score = overlap
            if product.slug and product.slug in candidate.filename_slug:
                score += 10
            if slug_text and candidate.filename_slug in slug_text:
                score += 4
            if f"product-{product.id}" in candidate.filename_slug:
                score += 20

            # Boost same subcategory/product-family words when present.
            sub_slug = getattr(product.subcategory, "slug", "") or ""
            if sub_slug:
                sub_tokens = self._tokenize(sub_slug)
                score += len(sub_tokens & candidate.tokens)

            if score >= min_score:
                scored.append((score, candidate))

        scored.sort(key=lambda item: (-item[0], item[1].key))

        unique: list[tuple[int, Candidate]] = []
        seen_urls: set[str] = set()
        for score, candidate in scored:
            if candidate.url in seen_urls:
                continue
            seen_urls.add(candidate.url)
            unique.append((score, candidate))
            if len(unique) >= max_images:
                break

        return unique

    def _product_tokens(self, product: Product) -> set[str]:
        tokens = set()
        tokens |= self._tokenize(product.slug or "")
        tokens |= self._tokenize(product.name or "")
        if product.meta_title:
            tokens |= self._tokenize(product.meta_title)
        if getattr(product, "subcategory", None):
            tokens |= self._tokenize(product.subcategory.slug or "")
        if getattr(product, "category", None):
            tokens |= self._tokenize(product.category.slug or "")
        return tokens

    def _tokenize(self, value: str) -> set[str]:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
        tokens = {token for token in normalized.split() if len(token) > 1 and token not in GENERIC_TOKENS}
        return tokens

    def _default_alt_text(self, product_name: str, candidate: Candidate) -> str:
        tail = candidate.filename_slug.replace("-", " ").strip()
        if tail and tail not in product_name.lower():
            return f"{product_name} {tail}"[:255]
        return product_name[:255]
