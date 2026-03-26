from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import (
    Category,
    Collection,
    HeroSlide,
    MattressOption,
    Order,
    Product,
    ProductColor,
    ProductFabric,
    ProductImage,
    ProductMattress,
    SubCategory,
)


IMAGE_FIELD_TARGETS = [
    (Category, "image"),
    (SubCategory, "image"),
    (Collection, "image"),
    (HeroSlide, "image"),
    (ProductImage, "url"),
    (ProductColor, "image_url"),
    (ProductFabric, "image_url"),
    (MattressOption, "image_url"),
    (ProductMattress, "image_url"),
]


@dataclass
class MigrationResult:
    scanned: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


class Command(BaseCommand):
    help = (
        "Copy existing media files referenced by DB image URLs into the active "
        "default storage backend (S3) and rewrite stored URLs to the S3 public URL."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-base-url",
            action="append",
            dest="source_base_urls",
            default=[],
            help=(
                "Base URL to use for relative /media/... records, for example "
                "https://reve-backend.onrender.com. Can be supplied multiple times."
            ),
        )
        parser.add_argument(
            "--timeout",
            type=float,
            default=30.0,
            help="HTTP timeout in seconds for downloading old media files.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without uploading files or updating the database.",
        )

    def handle(self, *args, **options):
        if not hasattr(settings, "STORAGES") or "default" not in settings.STORAGES:
            raise CommandError("Default storage is not configured.")

        base_urls = self._build_source_base_urls(options["source_base_urls"])
        result = MigrationResult()

        self.stdout.write(self.style.NOTICE("Starting media migration to S3"))
        self.stdout.write(f"Source base URLs: {', '.join(base_urls) if base_urls else '(none)'}")
        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run mode enabled; no files will be uploaded."))

        for model, field_name in IMAGE_FIELD_TARGETS:
            result = self._migrate_model_field(
                model=model,
                field_name=field_name,
                base_urls=base_urls,
                timeout=options["timeout"],
                dry_run=options["dry_run"],
                result=result,
            )

        result = self._migrate_product_dimension_images(
            base_urls=base_urls,
            timeout=options["timeout"],
            dry_run=options["dry_run"],
            result=result,
        )
        result = self._migrate_order_reference_images(
            base_urls=base_urls,
            timeout=options["timeout"],
            dry_run=options["dry_run"],
            result=result,
        )

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Migration pass finished"))
        self.stdout.write(f"Scanned: {result.scanned}")
        self.stdout.write(f"Updated: {result.updated}")
        self.stdout.write(f"Skipped: {result.skipped}")
        self.stdout.write(f"Failed: {result.failed}")

    def _build_source_base_urls(self, cli_urls: list[str]) -> list[str]:
        candidates = []
        for value in cli_urls + [getattr(settings, "BACKEND_URL", "")]:
            value = (value or "").strip().rstrip("/")
            if value and value not in candidates:
                candidates.append(value)

        # The old media inventory shows Render-hosted files; include it by default.
        render_default = "https://reve-backend.onrender.com"
        if render_default not in candidates:
            candidates.append(render_default)

        return candidates

    def _migrate_model_field(
        self,
        *,
        model,
        field_name: str,
        base_urls: list[str],
        timeout: float,
        dry_run: bool,
        result: MigrationResult,
    ) -> MigrationResult:
        queryset = model.objects.exclude(**{field_name: ""}).only("pk", field_name)
        for obj in queryset.iterator():
            result.scanned += 1
            current_url = getattr(obj, field_name, "")
            updated_url = self._migrate_single_url(
                current_url=current_url,
                base_urls=base_urls,
                timeout=timeout,
                dry_run=dry_run,
            )
            if updated_url is None:
                result.skipped += 1
                continue
            if updated_url is False:
                result.failed += 1
                continue
            if updated_url != current_url:
                if not dry_run:
                    setattr(obj, field_name, updated_url)
                    obj.save(update_fields=[field_name])
                result.updated += 1
            else:
                result.skipped += 1
        return result

    def _migrate_product_dimension_images(
        self,
        *,
        base_urls: list[str],
        timeout: float,
        dry_run: bool,
        result: MigrationResult,
    ) -> MigrationResult:
        queryset = Product.objects.exclude(dimension_images=[]).only("pk", "dimension_images")
        for product in queryset.iterator():
            images = product.dimension_images or []
            changed = False
            for entry in images:
                if not isinstance(entry, dict) or not entry.get("url"):
                    continue
                result.scanned += 1
                updated_url = self._migrate_single_url(
                    current_url=entry["url"],
                    base_urls=base_urls,
                    timeout=timeout,
                    dry_run=dry_run,
                )
                if updated_url is None:
                    result.skipped += 1
                    continue
                if updated_url is False:
                    result.failed += 1
                    continue
                if updated_url != entry["url"]:
                    entry["url"] = updated_url
                    changed = True
                    result.updated += 1
                else:
                    result.skipped += 1
            if changed and not dry_run:
                product.save(update_fields=["dimension_images"])
        return result

    def _migrate_order_reference_images(
        self,
        *,
        base_urls: list[str],
        timeout: float,
        dry_run: bool,
        result: MigrationResult,
    ) -> MigrationResult:
        queryset = Order.objects.exclude(reference_images=[]).only("pk", "reference_images")
        for order in queryset.iterator():
            refs = order.reference_images or []
            changed = False
            for idx, entry in enumerate(refs):
                if isinstance(entry, str):
                    current_url = entry
                    setter = lambda value, i=idx: refs.__setitem__(i, value)
                elif isinstance(entry, dict):
                    key = next(
                        (k for k in ("url", "publicUrl", "publicURL", "signedUrl", "signedURL") if entry.get(k)),
                        None,
                    )
                    if not key:
                        continue
                    current_url = entry[key]
                    setter = lambda value, e=entry, k=key: e.__setitem__(k, value)
                else:
                    continue

                result.scanned += 1
                updated_url = self._migrate_single_url(
                    current_url=current_url,
                    base_urls=base_urls,
                    timeout=timeout,
                    dry_run=dry_run,
                )
                if updated_url is None:
                    result.skipped += 1
                    continue
                if updated_url is False:
                    result.failed += 1
                    continue
                if updated_url != current_url:
                    setter(updated_url)
                    changed = True
                    result.updated += 1
                else:
                    result.skipped += 1
            if changed and not dry_run:
                order.save(update_fields=["reference_images"])
        return result

    def _migrate_single_url(
        self,
        *,
        current_url: str,
        base_urls: list[str],
        timeout: float,
        dry_run: bool,
    ) -> str | None | bool:
        current_url = (current_url or "").strip()
        if not current_url:
            return None

        if current_url.startswith("data:image/"):
            s3_url = self._migrate_data_url(current_url=current_url, dry_run=dry_run)
            return s3_url if s3_url is not None else False

        if self._is_s3_url(current_url):
            return current_url

        storage_name = self._storage_name_from_url(current_url)
        if not storage_name:
            self.stderr.write(f"Skipping unsupported media URL: {current_url}")
            return None

        s3_url = default_storage.url(storage_name)
        if self._is_s3_url(current_url):
            return current_url

        if dry_run:
            self.stdout.write(f"Would migrate: {current_url} -> {s3_url}")
            return s3_url

        if not default_storage.exists(storage_name):
            source_url = self._resolve_source_url(current_url, base_urls)
            if not source_url:
                self.stderr.write(f"Could not resolve source URL for: {current_url}")
                return False
            content = self._download_file(source_url, timeout=timeout)
            if content is None:
                return False
            default_storage.save(storage_name, ContentFile(content))

        self.stdout.write(f"Migrated: {current_url} -> {s3_url}")
        return s3_url

    def _migrate_data_url(self, *, current_url: str, dry_run: bool) -> str | None:
        header, _, payload = current_url.partition(",")
        if not payload or ";base64" not in header:
            self.stderr.write("Unsupported data URL format encountered.")
            return None

        try:
            content = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            self.stderr.write(f"Failed to decode data URL image: {exc}")
            return None

        extension = self._extension_from_data_url(header, content)
        storage_name = f"orders/reference-images/{uuid.uuid4().hex}.{extension}"
        s3_url = default_storage.url(storage_name)

        if dry_run:
            self.stdout.write(f"Would migrate data URL image -> {s3_url}")
            return s3_url

        default_storage.save(storage_name, ContentFile(content))
        self.stdout.write(f"Migrated data URL image -> {s3_url}")
        return s3_url

    def _storage_name_from_url(self, url: str) -> str | None:
        parsed = urlparse(url)
        path = parsed.path or url
        media_marker = "/media/"
        if path.startswith("media/"):
            return path[len("media/") :]
        if path.startswith("/media/"):
            return path[len(media_marker) :]
        if media_marker in path:
            return path.split(media_marker, 1)[1]
        return None

    def _resolve_source_url(self, current_url: str, base_urls: list[str]) -> str | None:
        parsed = urlparse(current_url)
        if parsed.scheme in {"http", "https"}:
            return current_url
        if current_url.startswith("/media/"):
            for base_url in base_urls:
                return f"{base_url}{current_url}"
        return None

    def _download_file(self, url: str, *, timeout: float) -> bytes | None:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            self.stderr.write(f"Failed to download {url}: {exc}")
            return None

    def _is_s3_url(self, url: str) -> bool:
        custom_domain = getattr(settings, "AWS_S3_CUSTOM_DOMAIN", "").strip()
        if custom_domain and custom_domain in url:
            return True
        return "amazonaws.com" in url and "/media/" in url

    def _extension_from_data_url(self, header: str, content: bytes) -> str:
        mime_part = header.split(";", 1)[0]
        mime_type = mime_part.split(":", 1)[1].lower() if ":" in mime_part else ""
        mime_map = {
            "image/jpeg": "jpg",
            "image/jpg": "jpg",
            "image/png": "png",
            "image/webp": "webp",
            "image/gif": "gif",
        }
        if mime_type in mime_map:
            return mime_map[mime_type]

        return "bin"
