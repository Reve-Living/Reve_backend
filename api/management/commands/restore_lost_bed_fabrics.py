from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.models import Product, ProductFabric


TARGET_SUBCATEGORY_SLUGS = (
    "divan-ottoman-beds",
    "upholstered-beds",
    "upholstered-ottoman-beds",
)

# Reuse intact fabric swatches from the closest matching Divan Bed Base design.
# These images are material swatches, not product gallery images.
SOURCE_PRODUCT_BY_TARGET = {
    57: 272,
    58: 262,
    60: 263,
    61: 261,
    220: 267,
    221: 268,
    222: 267,
    223: 273,
    224: 270,
    225: 264,
    226: 271,
    227: 271,
    228: 266,
    229: 265,
    63: 261,
    64: 261,
    66: 262,
    135: 263,
    236: 263,
    237: 263,
    238: 268,
    239: 261,
    240: 261,
    241: 265,
    68: 261,
    71: 261,
    242: 265,
    243: 261,
    244: 261,
    245: 268,
    246: 262,
    247: 263,
    248: 263,
}


class Command(BaseCommand):
    help = (
        "Restore missing fabrics for the affected Divan Ottoman and Upholstered "
        "bed subcategories. The command is a dry-run unless --apply is supplied."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create the missing ProductFabric rows after writing a JSON backup.",
        )

    def handle(self, *args, **options):
        products = list(
            Product.objects.filter(subcategory__slug__in=TARGET_SUBCATEGORY_SLUGS)
            .select_related("subcategory")
            .prefetch_related("fabrics")
            .order_by("subcategory__slug", "id")
        )
        if not products:
            raise CommandError("No products were found in the targeted subcategories.")

        unknown_ids = sorted(product.id for product in products if product.id not in SOURCE_PRODUCT_BY_TARGET)
        if unknown_ids:
            raise CommandError(
                "Recovery mapping is incomplete. No source product is configured for "
                f"target product IDs: {unknown_ids}"
            )

        source_ids = set(SOURCE_PRODUCT_BY_TARGET.values())
        sources = {
            product.id: product
            for product in Product.objects.filter(id__in=source_ids).prefetch_related("fabrics")
        }
        missing_sources = sorted(source_ids - set(sources))
        if missing_sources:
            raise CommandError(f"Source products do not exist: {missing_sources}")

        empty_sources = sorted(source_id for source_id, source in sources.items() if not source.fabrics.all())
        if empty_sources:
            raise CommandError(f"Source products have no fabrics: {empty_sources}")

        missing_fabrics = [product for product in products if not product.fabrics.all()]
        existing_fabrics = [product for product in products if product.fabrics.all()]

        for product in products:
            source = sources[SOURCE_PRODUCT_BY_TARGET[product.id]]
            status = "RESTORE" if product in missing_fabrics else "SKIP"
            self.stdout.write(
                f"[{status}] {product.id} {product.name} <- {source.id} {source.name}"
            )

        self.stdout.write("")
        self.stdout.write(
            f"targeted={len(products)} missing={len(missing_fabrics)} "
            f"already_present={len(existing_fabrics)}"
        )

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("DRY-RUN only. No database rows were changed."))
            return

        backup_path = self._write_backup(products)
        created = 0
        with transaction.atomic():
            for product in missing_fabrics:
                source = sources[SOURCE_PRODUCT_BY_TARGET[product.id]]
                for fabric in source.fabrics.all():
                    ProductFabric.objects.create(
                        product=product,
                        name=fabric.name,
                        image_url=fabric.image_url,
                        is_shared=fabric.is_shared,
                        colors=copy.deepcopy(fabric.colors),
                    )
                    created += 1

        cache.clear()
        self.stdout.write(
            self.style.SUCCESS(
                f"Restored {created} fabric rows across {len(missing_fabrics)} products."
            )
        )
        self.stdout.write(f"Backup: {backup_path}")

    def _write_backup(self, products: list[Product]) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = Path(settings.BASE_DIR) / f"tmp_fabric_restore_backup_{timestamp}.json"
        payload = []

        for product in products:
            payload.append(
                {
                    "id": product.id,
                    "name": product.name,
                    "subcategory": product.subcategory.slug,
                    "fabrics": [
                        {
                            "id": fabric.id,
                            "name": fabric.name,
                            "image_url": fabric.image_url,
                            "is_shared": fabric.is_shared,
                            "colors": fabric.colors,
                        }
                        for fabric in product.fabrics.all()
                    ],
                }
            )

        backup_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return backup_path
