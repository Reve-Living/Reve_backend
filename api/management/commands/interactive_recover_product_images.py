from __future__ import annotations

import re
from dataclasses import dataclass

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
    "high",
    "gloss",
    "table",
    "chair",
    "chairs",
    "coffee",
    "console",
    "dining",
    "sideboard",
    "bed",
    "storage",
}


@dataclass
class Candidate:
    key: str
    url: str
    score: int
    overlap: list[str]


class Command(BaseCommand):
    help = (
        "Interactively recover product images from S3 by showing likely candidates "
        "and letting you choose which ones to attach."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--product-id",
            type=int,
            action="append",
            dest="product_ids",
            default=[],
            help="Restrict to specific product IDs. Can be supplied multiple times.",
        )
        parser.add_argument(
            "--subcategory",
            action="append",
            dest="subcategories",
            default=[],
            help="Restrict to specific subcategory slugs. Can be supplied multiple times.",
        )
        parser.add_argument(
            "--replace-existing",
            action="store_true",
            help="Delete a product's existing images before saving your selected ones.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Process only the first N filtered products. 0 means no limit.",
        )
        parser.add_argument(
            "--max-candidates",
            type=int,
            default=8,
            help="Maximum number of candidate images to show per product.",
        )
        parser.add_argument(
            "--min-score",
            type=int,
            default=2,
            help="Minimum token-overlap score to show a candidate.",
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

        keys = self._load_s3_keys(client, bucket, media_url)
        if not keys:
            raise CommandError("No image files were found in the S3 media/ prefix.")

        products = Product.objects.select_related("subcategory").all().order_by("id")
        if options["product_ids"]:
            products = products.filter(id__in=options["product_ids"])
        if options["subcategories"]:
            products = products.filter(subcategory__slug__in=options["subcategories"])
        if options["limit"]:
            products = products[: options["limit"]]

        saved = 0
        skipped = 0

        for product in products:
            candidates = self._find_candidates(
                product,
                keys,
                max_candidates=options["max_candidates"],
                min_score=options["min_score"],
            )
            self.stdout.write("")
            self.stdout.write(self.style.NOTICE(f"Product {product.id}: {product.name}"))
            self.stdout.write(f"Current images: {product.images.count()}")

            if not candidates:
                self.stdout.write(self.style.WARNING("  No candidates found."))
                skipped += 1
                continue

            for idx, candidate in enumerate(candidates):
                self.stdout.write(
                    f"  [{idx}] score={candidate.score:02d} overlap={','.join(candidate.overlap)}"
                )
                self.stdout.write(f"      {candidate.key}")
                self.stdout.write(f"      {candidate.url}")

            self.stdout.write(
                "Choose indexes separated by commas, `s` to skip, `q` to quit, or press Enter to skip."
            )
            raw = input("> ").strip().lower()

            if raw in {"q", "quit"}:
                break
            if raw in {"", "s", "skip"}:
                skipped += 1
                continue

            try:
                picks = []
                for part in raw.split(","):
                    idx = int(part.strip())
                    if idx < 0 or idx >= len(candidates):
                        raise ValueError
                    picks.append(candidates[idx])
            except ValueError:
                self.stdout.write(self.style.ERROR("  Invalid selection, skipping this product."))
                skipped += 1
                continue

            with transaction.atomic():
                if options["replace_existing"]:
                    product.images.all().delete()
                for candidate in picks:
                    ProductImage.objects.create(
                        product=product,
                        url=candidate.url,
                        alt_text=self._alt_text(product.name, candidate),
                    )
                    saved += 1

            self.stdout.write(self.style.SUCCESS(f"  Saved {len(picks)} image(s)."))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Done. saved_rows={saved} skipped_products={skipped}"))

    def _load_s3_keys(self, client, bucket: str, media_url: str) -> list[tuple[str, str, set[str]]]:
        paginator = client.get_paginator("list_objects_v2")
        keys: list[tuple[str, str, set[str]]] = []
        for page in paginator.paginate(Bucket=bucket, Prefix="media/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                    continue
                relative_key = key.split("media/", 1)[1] if "media/" in key else key
                filename = key.rsplit("/", 1)[-1]
                filename = UUID_PREFIX_RE.sub("", filename)
                filename_slug = EXT_RE.sub("", filename).replace("_result_result", "").replace("_result", "")
                tokens = self._tokenize(filename_slug)
                if not tokens:
                    continue
                keys.append((key, f"{media_url}{relative_key}", tokens))
        return keys

    def _find_candidates(self, product: Product, keys, *, max_candidates: int, min_score: int) -> list[Candidate]:
        product_tokens = self._product_tokens(product)
        family_tokens = self._family_tokens(product)
        candidates: list[Candidate] = []
        seen_urls: set[str] = set()

        for key, url, tokens in keys:
            overlap = sorted(product_tokens & tokens)
            family_overlap = sorted(family_tokens & tokens)
            score = len(overlap) + (2 * len(family_overlap))
            if product.slug and product.slug in key:
                score += 20
            if f"product-{product.id}" in key:
                score += 30
            if score < min_score:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            candidates.append(Candidate(key=key, url=url, score=score, overlap=overlap))

        candidates.sort(key=lambda item: (-item.score, item.key))
        return candidates[:max_candidates]

    def _product_tokens(self, product: Product) -> set[str]:
        tokens = set()
        tokens |= self._tokenize(product.slug or "")
        tokens |= self._tokenize(product.name or "")
        if product.meta_title:
            tokens |= self._tokenize(product.meta_title)
        return tokens

    def _family_tokens(self, product: Product) -> set[str]:
        base = (product.slug or product.name or "").lower()
        parts = [part for part in re.split(r"[^a-z0-9]+", base) if part]
        return {part for part in parts[:2] if part not in GENERIC_TOKENS}

    def _tokenize(self, value: str) -> set[str]:
        normalized = re.sub(r"[^a-z0-9]+", " ", value.lower())
        return {token for token in normalized.split() if len(token) > 1 and token not in GENERIC_TOKENS}

    def _alt_text(self, product_name: str, candidate: Candidate) -> str:
        cleaned = candidate.key.rsplit("/", 1)[-1]
        cleaned = UUID_PREFIX_RE.sub("", cleaned)
        cleaned = EXT_RE.sub("", cleaned).replace("_result_result", "").replace("_result", "")
        cleaned = cleaned.replace("-", " ").replace("_", " ").strip()
        if cleaned and cleaned.lower() not in product_name.lower():
            return f"{product_name} {cleaned}"[:255]
        return product_name[:255]
