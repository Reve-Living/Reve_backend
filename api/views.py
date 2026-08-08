import uuid
import os
import stripe
import re
import logging
import time
import threading
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.db import connection, transaction
from django.db.models import Avg, BooleanField, Count, DecimalField, Exists, FloatField, IntegerField, OuterRef, Prefetch, Q, Subquery, Case, When, Value
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.http import HttpResponse, StreamingHttpResponse
from xml.sax.saxutils import escape
from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound, ValidationError
from django.utils import timezone

from .models import (
    Category,
    SubCategory,
    Product,
    ProductImage,
    ProductVideo,
    ProductColor,
    ProductSize,
    ProductStyle,
    ProductFabric,
    ProductMattress,
    ProductAddon,
    MattressOption,
    MattressOptionPrice,
    Order,
    OrderItem,
    Review,
    Collection,
    FilterType,
    FilterOption,
    CategoryFilter,
    ProductFilterValue,
    DimensionTemplate,
    ProductDimensionTemplate,
    HeroSlide,
    LifestyleSection,
    LifestyleArticle,
    Promotion,
    AnnouncementSettings,
)
from .serializers import (
    RegisterSerializer,
    CategorySerializer,
    SubCategorySerializer,
    ProductSerializer,
    ProductQuickSerializer,
    ProductSummarySerializer,
    ProductAdminDetailSerializer,
    ProductAdminListSerializer,
    ProductAdminPickerSerializer,
    ProductWriteSerializer,
    OrderSerializer,
    ReviewSerializer,
    CollectionSerializer,
    HeroSlideSerializer,
    LifestyleSectionSerializer,
    LifestyleArticleSerializer,
    PromotionSerializer,
    AnnouncementSettingsSerializer,
    FilterTypeSerializer,
    FilterOptionSerializer,
    CategoryFilterSerializer,
    ProductFilterValueSerializer,
    ProductStyleLibrarySerializer,
    MattressOptionSerializer,
    ProductMattressSerializer,
    ProductAddonSerializer,
)
from .emails import send_order_cancellation_emails, send_order_confirmation_emails
from .delivery_note_pdf import build_delivery_note_pdf
from .payments import (
    PaymentProviderError,
    extract_paypal_capture,
    extract_local_order_id_from_paypal,
    extract_paypal_capture_id,
    get_stripe_payment_details,
    paypal_access_token,
    paypal_request,
    refund_paypal_payment,
    refund_stripe_payment,
    resolve_paypal_payment_details,
)


TWOPLACES = Decimal("0.01")
PRODUCT_LIST_CACHE_TTL = int(os.getenv("PRODUCT_LIST_CACHE_TTL", "1800"))
PRODUCT_DETAIL_CACHE_TTL = int(os.getenv("PRODUCT_DETAIL_CACHE_TTL", "60"))
CATEGORY_FILTER_CACHE_TTL = int(os.getenv("CATEGORY_FILTER_CACHE_TTL", "900"))
PRODUCT_LIST_PAGE_SIZE = int(os.getenv("PRODUCT_LIST_PAGE_SIZE", "24"))
PRODUCT_SQL_DEBUG_LOG = os.getenv("PRODUCT_SQL_DEBUG_LOG", "False") == "True"
logger = logging.getLogger(__name__)


def _has_usable_cache_backend() -> bool:
    backend = str((settings.CACHES.get("default") or {}).get("BACKEND", "")).strip()
    return bool(backend) and backend != "django.core.cache.backends.dummy.DummyCache"


def _wants_empty_success_response(request) -> bool:
    return str(request.query_params.get("response") or "").strip().lower() == "none"


def _positive_int_query_param(request, key, maximum=100):
    try:
        value = int(str(request.query_params.get(key) or "").strip())
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return min(value, maximum)


def _nonnegative_int_query_param(request, key, maximum=10000):
    try:
        value = int(str(request.query_params.get(key) or "").strip())
    except (TypeError, ValueError):
        return 0
    if value <= 0:
        return 0
    return min(value, maximum)


def _bounded_int_query_param(request, key, default, minimum=1, maximum=100):
    try:
        value = int(str(request.query_params.get(key) or "").strip())
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _primary_image_subquery():
    return (
        ProductImage.objects.filter(product_id=OuterRef("pk"))
        .order_by("sort_order", "id")
        .values("url")[:1]
    )


def _primary_image_flip_subquery():
    return (
        ProductImage.objects.filter(product_id=OuterRef("pk"))
        .order_by("sort_order", "id")
        .values("flip_horizontal")[:1]
    )


def _min_size_price_subquery():
    return ProductSize.objects.filter(product_id=OuterRef("pk")).order_by("price_delta").values("price_delta")[:1]


def _size_count_subquery():
    return (
        ProductSize.objects.filter(product_id=OuterRef("pk"))
        .order_by()
        .values("product_id")
        .annotate(total=Count("id"))
        .values("total")[:1]
    )


def _slowest_sql_query(query_start):
    queries = connection.queries[query_start:]
    if not queries:
        return None
    slowest = max(queries, key=lambda query: float(query.get("time") or 0))
    sql = str(slowest.get("sql") or "").replace("\n", " ")
    if len(sql) > 500:
        sql = f"{sql[:500]}..."
    return {
        "time_ms": float(slowest.get("time") or 0) * 1000,
        "sql": sql,
    }


def _with_live_review_summary(queryset):
    visible_reviews = (
        Review.objects.filter(product_id=OuterRef("pk"), is_visible=True)
        .order_by()
        .values("product_id")
    )
    return queryset.annotate(
        live_rating=Subquery(
            visible_reviews.annotate(average_rating=Avg("rating")).values("average_rating")[:1],
            output_field=FloatField(),
        ),
        live_review_count=Subquery(
            visible_reviews.annotate(total_reviews=Count("id")).values("total_reviews")[:1],
            output_field=IntegerField(),
        ),
    )


def _as_decimal(value, default="0.00"):
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _coerce_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _should_confirm_public_order_on_create(payment_method: str | None) -> bool:
    return str(payment_method or "").strip().lower() in ("cod", "cash_on_delivery")


def _queue_order_confirmation_email(order: Order) -> None:
    if order.confirmation_email_sent_at:
        return

    order.confirmation_email_sent_at = timezone.now()
    order.save(update_fields=["confirmation_email_sent_at"])
    transaction.on_commit(lambda: send_order_confirmation_emails(order.id))


def _get_live_promotions():
    today = timezone.localdate()
    return (
        Promotion.objects.filter(is_active=True, start_date__lte=today, end_date__gte=today)
        .prefetch_related("categories", "subcategories")
        .order_by("sort_order", "start_date", "name")
    )


def _promotion_applies_to_product(promotion: Promotion, product: Product) -> bool:
    category_ids = [category.id for category in promotion.categories.all()]
    subcategory_ids = [subcategory.id for subcategory in promotion.subcategories.all()]
    if not category_ids and not subcategory_ids:
        return True
    if product.subcategory_id and product.subcategory_id in subcategory_ids:
        return True
    if product.category_id in category_ids:
        return True
    return False


def _serialize_public_promotion(promotion: Promotion) -> dict:
    return {
        "id": promotion.id,
        "name": promotion.name,
        "announcement_text": promotion.announcement_text,
        "discount_percentage": float(promotion.discount_percentage),
        "start_date": promotion.start_date,
        "end_date": promotion.end_date,
        "category_ids": [category.id for category in promotion.categories.all()],
        "subcategory_ids": [subcategory.id for subcategory in promotion.subcategories.all()],
    }


def _build_promotion_result(*, code: str, items_payload: list[dict]):
    normalized_code = str(code or "").strip().upper()
    if not normalized_code:
        raise ValidationError({"code": "Promo code is required"})

    promotion = _get_live_promotions().filter(code__iexact=normalized_code).first()
    if not promotion:
        raise ValidationError({"code": "This promo code is invalid or not active right now"})

    product_ids = [item.get("product_id") for item in items_payload if item.get("product_id")]
    products = Product.objects.filter(id__in=product_ids).select_related("category", "subcategory")
    product_lookup = {product.id: product for product in products}

    applicable_subtotal = Decimal("0.00")
    subtotal = Decimal("0.00")
    line_results = []
    applicable_product_ids = set()

    for index, item in enumerate(items_payload):
        product_id = item.get("product_id")
        quantity = max(int(item.get("quantity", 1) or 1), 1)
        unit_price = _as_decimal(item.get("price"))
        line_subtotal = _round_money(unit_price * quantity)
        subtotal += line_subtotal
        product = product_lookup.get(product_id)
        is_applicable = bool(product and _promotion_applies_to_product(promotion, product))
        if is_applicable:
            applicable_subtotal += line_subtotal
            applicable_product_ids.add(product_id)
        line_results.append(
            {
                "index": index,
                "product_id": product_id,
                "quantity": quantity,
                "unit_price": float(_round_money(unit_price)),
                "line_subtotal": float(line_subtotal),
                "is_applicable": is_applicable,
            }
        )

    if applicable_subtotal <= Decimal("0.00"):
        raise ValidationError({"code": "This promo code is not valid for the selected products"})

    discount_percentage = _as_decimal(promotion.discount_percentage)
    discount_amount = _round_money(applicable_subtotal * discount_percentage / Decimal("100"))

    return {
        "promotion": promotion,
        "subtotal": _round_money(subtotal),
        "applicable_subtotal": _round_money(applicable_subtotal),
        "discount_amount": discount_amount,
        "discount_percentage": discount_percentage,
        "applicable_product_ids": applicable_product_ids,
        "line_results": line_results,
    }


def _get_announcement_settings():
    return AnnouncementSettings.get_solo()


def _normalize_base_url(raw_url: str, fallback: str = "") -> str:
    base = (raw_url or fallback or "").strip()
    if not base:
        return ""
    if not re.match(r"^https?://", base, flags=re.IGNORECASE):
        base = f"https://{base}"
    return f"{base.rstrip('/')}/"


def _to_absolute_url(raw_url: str, base_url: str) -> str:
    value = (raw_url or "").strip()
    if not value:
        return ""
    if value.startswith(("http://", "https://")):
        return value
    if value.startswith("//"):
        return f"https:{value}"
    return urljoin(base_url, value.lstrip("/"))


def _to_google_feed_image_url(raw_url: str, base_url: str) -> str:
    absolute_url = _to_absolute_url(raw_url, base_url)
    if not re.match(r"^https?://", absolute_url, flags=re.IGNORECASE):
        return ""

    parts = urlsplit(absolute_url)
    if not parts.netloc:
        return ""

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        quote(parts.path, safe="/%:@"),
        quote(parts.query, safe="=&%:@/?"),
        "",
    ))


# Google Merchant Center category mapping
GOOGLE_PRODUCT_CATEGORY_MAP = {
    # Beds & Mattresses
    "beds": "Furniture > Beds & Bed Frames",
    "bed frames": "Furniture > Beds & Bed Frames",
    "kids beds": "Furniture > Beds & Bed Frames",
    "storage beds": "Furniture > Beds & Bed Frames",
    "ottoman beds": "Furniture > Beds & Bed Frames",
    "upholstered beds": "Furniture > Beds & Bed Frames",
    "upholstered ottoman beds": "Furniture > Beds & Bed Frames",
    "wooden beds": "Furniture > Beds & Bed Frames",
    "bunk beds": "Furniture > Beds & Bed Frames",
    "cabin beds": "Furniture > Beds & Bed Frames",
    "day beds": "Furniture > Beds & Bed Frames",
    "mattresses": "Furniture > Mattresses",
    "mattress": "Furniture > Mattresses",
    # Sofas & Seating
    "sofas": "Furniture > Sofas & Couches",
    "reclining sofas": "Furniture > Sofas & Couches",
    "corner sofas": "Furniture > Sofas & Couches",
    "sofa beds": "Furniture > Futons",
    "armchairs": "Furniture > Armchairs & Accent Chairs",
    "recliner armchairs": "Furniture > Armchairs & Accent Chairs",
    "seating": "Furniture > Seating",
    "chairs": "Furniture > Chairs",
    "dining chairs": "Furniture > Chairs",
    "stools": "Furniture > Ottomans & Poufs",
    # Storage
    "storage": "Furniture > Storage & Organization",
    "wardrobes": "Furniture > Dressers & Chest of Drawers",
    "shelving": "Furniture > Shelves & Shelving Units",
    # Tables & Desks
    "tables": "Furniture > Tables",
    "dining": "Furniture > Dining Tables",
    "dining tables": "Furniture > Dining Tables",
    "coffee-tables": "Furniture > Coffee Tables",
    "coffee tables": "Furniture > Coffee Tables",
    "desks": "Furniture > Desks",
    # Default fallback
    "default": "Furniture",
}

GOOGLE_FEED_LOWEST_PRICE_GROUPS = {
    "divan beds",
    "divan ottoman beds",
    "mattress",
    "mattresses",
    "upholstered beds",
    "upholstered ottoman beds",
    "wooden beds",
    "trundle beds",
    "underbed storage",
}


def _get_google_product_category(category_name: str) -> str:
    """Map internal category to Google Merchant category."""
    if not category_name:
        return GOOGLE_PRODUCT_CATEGORY_MAP["default"]
    
    category_lower = category_name.lower().strip()
    
    # Exact match
    if category_lower in GOOGLE_PRODUCT_CATEGORY_MAP:
        return GOOGLE_PRODUCT_CATEGORY_MAP[category_lower]
    
    # Substring match
    for key, value in GOOGLE_PRODUCT_CATEGORY_MAP.items():
        if key != "default" and key in category_lower:
            return value
    
    return GOOGLE_PRODUCT_CATEGORY_MAP["default"]


def _normalized_feed_group_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _uses_lowest_google_feed_price(product) -> bool:
    category_name = _normalized_feed_group_name(getattr(getattr(product, "category", None), "name", ""))
    subcategory_name = _normalized_feed_group_name(getattr(getattr(product, "subcategory", None), "name", ""))
    return category_name in GOOGLE_FEED_LOWEST_PRICE_GROUPS or subcategory_name in GOOGLE_FEED_LOWEST_PRICE_GROUPS


def _get_lowest_google_feed_price(product, sizes=None) -> Decimal:
    prices = [Decimal(product.price)]
    size_queryset = getattr(product, "sizes", None)
    size_items = sizes if sizes is not None else (size_queryset.all() if size_queryset is not None else [])
    for size in size_items:
        if getattr(size, "price_delta", None):
            prices.append(Decimal(size.price_delta))
    return min(prices)


def _get_google_feed_product_text(product) -> str:
    category_name = getattr(getattr(product, "category", None), "name", "")
    subcategory_name = getattr(getattr(product, "subcategory", None), "name", "")
    features = getattr(product, "features", "")
    if isinstance(features, list):
        features = " ".join(str(item or "") for item in features)
    return " ".join(
        str(value or "")
        for value in [
            getattr(product, "name", ""),
            getattr(product, "slug", ""),
            category_name,
            subcategory_name,
            getattr(product, "short_description", ""),
            getattr(product, "description", ""),
            features,
        ]
    ).lower()


GOOGLE_FEED_FRAME_MATERIAL_PATTERNS = (
    (r"\bengineered wood frame\b", "Engineered Wood"),
    (r"\bsolid rubberwood\b|\brubberwood frame\b", "Solid Rubberwood"),
    (r"\bsolid wood frame\b|\bwooden frame\b|\bwood frame\b", "Solid Wood"),
    (r"\bmetal frame\b|\bsteel frame\b|\biron frame\b", "Metal"),
)


GOOGLE_FEED_COLOUR_PHRASES = (
    ("dark grey", "Dark Grey"),
    ("light grey", "Light Grey"),
    ("stone grey", "Stone Grey"),
    ("grey", "Grey"),
    ("gray", "Gray"),
    ("charcoal", "Charcoal"),
    ("silver", "Silver"),
    ("white", "White"),
    ("black", "Black"),
    ("cream", "Cream"),
    ("beige", "Beige"),
    ("brown", "Brown"),
    ("blue", "Blue"),
    ("green", "Green"),
    ("pink", "Pink"),
)


GOOGLE_FEED_FABRIC_TYPE_PHRASES = (
    ("plush velvet", "Plush Velvet"),
    ("crushed velvet", "Crushed Velvet"),
    ("velvet fabric", "Velvet Fabric"),
    ("micro fibre fabric", "Micro Fibre Fabric"),
    ("microfibre fabric", "Microfibre Fabric"),
    ("micro fibre", "Micro Fibre"),
    ("microfiber", "Microfiber"),
    ("bonded leather", "Bonded Leather"),
    ("chenille", "Chenille"),
    ("conistan", "Conistan"),
    ("aire leather", "Aire Leather"),
    ("pu leather effect", "PU Leather Effect"),
    ("leather effect", "Leather Effect"),
    ("linen", "Linen"),
    ("velvet", "Velvet"),
    ("fabric", "Fabric"),
)


def _extract_google_feed_phrase_values(text: str, phrases: tuple) -> list:
    normalised_text = f" {_normalise_google_feed_detail_name(text)} "
    values = []
    matched_spans = []
    for phrase, label in phrases:
        match = re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", normalised_text)
        if match and not any(match.start() < end and match.end() > start for start, end in matched_spans):
            values.append(label)
            matched_spans.append(match.span())
    return values


def _extract_google_feed_frame_material(product_text: str) -> str:
    for pattern, value in GOOGLE_FEED_FRAME_MATERIAL_PATTERNS:
        if re.search(pattern, product_text):
            return value
    return ""


def _get_google_feed_manual_value(product, field_name: str) -> str:
    return str(getattr(product, field_name, "") or "").strip()


def _get_google_feed_variant_value(variant: dict | None, field_name: str) -> str:
    if not isinstance(variant, dict):
        return ""
    return str(variant.get(field_name) or "").strip()


def _get_google_feed_manual_variants(product) -> list:
    raw_variants = getattr(product, "google_feed_variants", []) or []
    if not isinstance(raw_variants, list):
        return []

    variants = []
    for variant in raw_variants:
        if not isinstance(variant, dict):
            continue
        cleaned_variant = {
            "color": _get_google_feed_variant_value(variant, "color"),
            "fabric": _get_google_feed_variant_value(variant, "fabric"),
            "size": _get_google_feed_variant_value(variant, "size"),
            "sku": _get_google_feed_variant_value(variant, "sku"),
            "mpn": _get_google_feed_variant_value(variant, "mpn"),
            "gtin": _get_google_feed_variant_value(variant, "gtin"),
            "price": _get_google_feed_variant_value(variant, "price"),
        }
        if any(cleaned_variant.values()):
            variants.append(cleaned_variant)
    return variants


def _get_google_feed_variant_price(variant: dict | None) -> Decimal | None:
    raw_price = _get_google_feed_variant_value(variant, "price")
    if not raw_price:
        return None
    try:
        price = Decimal(raw_price)
    except (InvalidOperation, TypeError, ValueError):
        return None
    if price <= 0:
        return None
    return price


def _normalise_google_feed_match_value(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _get_google_feed_variant_image_url(product, manual_variant: dict | None, backend_base_url: str) -> str:
    images = getattr(product, "_prefetched_images", []) or []
    variant_values = [
        _get_google_feed_variant_value(manual_variant, "color"),
        _get_google_feed_variant_value(manual_variant, "fabric"),
        _get_google_feed_variant_value(manual_variant, "size"),
    ]
    normalised_variant_values = {
        _normalise_google_feed_match_value(value)
        for value in variant_values
        if _normalise_google_feed_match_value(value)
    }

    if normalised_variant_values:
        for image in images:
            image_values = [
                getattr(image, "color_name", ""),
                getattr(image, "style_name", ""),
            ]
            normalised_image_values = {
                _normalise_google_feed_match_value(value)
                for value in image_values
                if _normalise_google_feed_match_value(value)
            }
            if normalised_variant_values.intersection(normalised_image_values):
                image_url = _to_google_feed_image_url(getattr(image, "url", ""), backend_base_url)
                if image_url:
                    return image_url

    return _to_google_feed_image_url(getattr(product, "primary_image_url", ""), backend_base_url)


def _get_google_feed_age_group(product) -> str:
    category_name = getattr(getattr(product, "category", None), "name", "")
    category_slug = getattr(getattr(product, "category", None), "slug", "")
    subcategory_name = getattr(getattr(product, "subcategory", None), "name", "")
    subcategory_slug = getattr(getattr(product, "subcategory", None), "slug", "")
    scope_text = _normalise_google_feed_detail_name(
        " ".join([category_name, category_slug, subcategory_name, subcategory_slug])
    )
    kids_terms = (
        "kids",
        "children",
        "child",
        "baby",
        "toddler",
        "cot",
        "bunk",
        "cabin",
        "sleeper",
        "novelty",
        "day bed",
        "day-bed",
        "trundle",
    )
    if any(term in scope_text for term in kids_terms):
        return "kids"
    return "adult"


def _get_google_feed_age_range_detail(product) -> str:
    product_text = _get_google_feed_product_text(product)
    if "bed" not in product_text or "sofa bed" in product_text:
        return ""
    if _get_google_feed_age_group(product) == "kids":
        return "Newborn to 14 years old"
    return "10+ years old"


def _get_google_feed_extra_attributes(product, materials) -> dict:
    product_text = _get_google_feed_product_text(product)
    is_bed = "bed" in product_text and "sofa bed" not in product_text
    is_ottoman = "ottoman" in product_text

    frame_material = _extract_google_feed_frame_material(product_text)
    headboard_material = ""
    number_of_drawers = ""

    if is_bed and materials:
        headboard_material = ", ".join(materials)

    if is_bed and is_ottoman:
        number_of_drawers = "0"

    return {
        "frame_material": _get_google_feed_manual_value(product, "google_feed_frame_material") or frame_material,
        "headboard_material": _get_google_feed_manual_value(product, "google_feed_headboard_material") or headboard_material,
        "number_of_drawers": _get_google_feed_manual_value(product, "google_feed_number_of_drawers") or number_of_drawers,
    }


GOOGLE_FEED_DIMENSION_NAMES = {
    "depth": "Depth",
    "length": "Length",
    "seat height": "Seat Height",
    "seat_height": "Seat Height",
    "seat-height": "Seat Height",
    "width": "Width",
    "height": "Height",
    "bed height": "Bed Height",
    "headboard height": "Headboard Height",
}


GOOGLE_FEED_DIMENSION_PARAGRAPH_PATTERNS = {
    "Depth": r"\bdepth\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?\s*(?:cm|mm|m|in|inch|inches))\b",
    "Length": r"\blength\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?\s*(?:cm|mm|m|in|inch|inches))\b",
    "Width": r"\bwidth\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?\s*(?:cm|mm|m|in|inch|inches))\b",
    "Height": r"(?<!seat )(?<!bed )(?<!headboard )\bheight\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?\s*(?:cm|mm|m|in|inch|inches))\b",
    "Seat Height": r"\bseat\s*height\s*[:\-]\s*([0-9]+(?:\.[0-9]+)?\s*(?:cm|mm|m|in|inch|inches))\b",
}


GOOGLE_FEED_LENGTH_FROM_WIDTH_PRODUCT_TERMS = (
    "sofa",
    "recliner",
    "armchair",
    "chair",
)


def _normalise_google_feed_detail_name(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).lower()


def _append_google_feed_product_detail(details: list, section_name: str, attribute_name: str, attribute_value: str) -> None:
    attribute_name = str(attribute_name or "").strip()
    attribute_value = str(attribute_value or "").strip()
    if not attribute_name or not attribute_value:
        return

    key = (
        _normalise_google_feed_detail_name(section_name),
        _normalise_google_feed_detail_name(attribute_name),
        _normalise_google_feed_detail_name(attribute_value),
    )
    if any(item["_key"] == key for item in details):
        return

    details.append({
        "section_name": str(section_name or "").strip() or "Specifications",
        "attribute_name": attribute_name,
        "attribute_value": attribute_value,
        "_key": key,
    })


def _get_google_feed_filter_details(product) -> dict:
    grouped_values = {}
    filter_values = getattr(product, "_prefetched_filter_values", [])
    for product_filter_value in filter_values:
        option = getattr(product_filter_value, "filter_option", None)
        filter_type = getattr(option, "filter_type", None)
        filter_name = getattr(filter_type, "name", "")
        option_name = getattr(option, "name", "")
        if not filter_name or not option_name:
            continue
        grouped_values.setdefault(_normalise_google_feed_detail_name(filter_name), [])
        if option_name not in grouped_values[_normalise_google_feed_detail_name(filter_name)]:
            grouped_values[_normalise_google_feed_detail_name(filter_name)].append(option_name)
    return grouped_values


def _get_google_feed_filter_values(filter_details: dict, keywords: tuple) -> list:
    values = []
    for filter_name, option_names in filter_details.items():
        if any(keyword in filter_name for keyword in keywords):
            for option_name in option_names:
                if option_name not in values:
                    values.append(option_name)
    return values


def _has_google_feed_detail_attribute(details: list, attribute_name: str) -> bool:
    normalised_attribute_name = _normalise_google_feed_detail_name(attribute_name)
    return any(_normalise_google_feed_detail_name(item.get("attribute_name")) == normalised_attribute_name for item in details)


def _get_google_feed_dimension_value(row: dict, size=None) -> str:
    values = row.get("values", {}) if isinstance(row, dict) else {}
    if not isinstance(values, dict):
        return ""

    if size:
        size_name = str(getattr(size, "name", "") or "").strip()
        if size_name:
            for key, value in values.items():
                if str(key).strip().lower() == size_name.lower() and str(value or "").strip():
                    return str(value).strip()

    cleaned_values = [
        (str(key).strip(), str(value).strip())
        for key, value in values.items()
        if str(key).strip() and str(value or "").strip()
    ]
    if not cleaned_values:
        return ""

    unique_values = list(dict.fromkeys(value for _key, value in cleaned_values))
    if len(unique_values) == 1:
        return unique_values[0]

    return "; ".join(f"{key}: {value}" for key, value in cleaned_values)


def _get_google_feed_dimension_paragraph_details(product) -> list:
    dimension_text = " ".join(
        str(value or "")
        for value in [
            getattr(product, "dimension_paragraph", ""),
            getattr(product, "dimension_note", ""),
        ]
    )
    if not dimension_text.strip():
        return []

    details = []
    for attribute_name, pattern in GOOGLE_FEED_DIMENSION_PARAGRAPH_PATTERNS.items():
        match = re.search(pattern, dimension_text, flags=re.IGNORECASE)
        if match:
            _append_google_feed_product_detail(details, "Dimensions", attribute_name, match.group(1))

    has_length = _has_google_feed_detail_attribute(details, "Length")
    has_width = _has_google_feed_detail_attribute(details, "Width")
    product_name = _normalise_google_feed_detail_name(getattr(product, "name", ""))
    if not has_length and has_width and any(term in product_name for term in GOOGLE_FEED_LENGTH_FROM_WIDTH_PRODUCT_TERMS):
        width_detail = next(
            (
                item
                for item in details
                if _normalise_google_feed_detail_name(item.get("attribute_name")) == "width"
            ),
            None,
        )
        if width_detail:
            _append_google_feed_product_detail(details, "Dimensions", "Length", width_detail["attribute_value"])

    return details


def _get_google_feed_product_details(
    product,
    size,
    material_text: str,
    color_text: str,
    extra_attributes: dict,
    extracted_color_text: str = "",
    extracted_fabric_text: str = "",
) -> list:
    details = []

    product_type = getattr(getattr(product, "subcategory", None), "name", "") or getattr(getattr(product, "category", None), "name", "")
    _append_google_feed_product_detail(details, "General", "Product Type", product_type)

    if color_text:
        _append_google_feed_product_detail(details, "General", "Colour", color_text)
    if material_text:
        _append_google_feed_product_detail(details, "General", "Material", material_text)
    if extra_attributes.get("frame_material"):
        _append_google_feed_product_detail(details, "General", "Frame Material", extra_attributes["frame_material"])
    if extra_attributes.get("headboard_material"):
        _append_google_feed_product_detail(details, "General", "Headboard Material", extra_attributes["headboard_material"])
    if extra_attributes.get("number_of_drawers") != "":
        _append_google_feed_product_detail(details, "General", "Number of Drawers", extra_attributes["number_of_drawers"])
    if extracted_fabric_text:
        _append_google_feed_product_detail(details, "General", "Fabric Type", extracted_fabric_text)

    filter_details = _get_google_feed_filter_details(product)
    for filter_name, option_names in filter_details.items():
        joined_options = ", ".join(option_names)
        if "fabric" in filter_name:
            if not _has_google_feed_detail_attribute(details, "Fabric Type"):
                _append_google_feed_product_detail(details, "General", "Fabric Type", joined_options)
        elif "upholstery" in filter_name or "finish" in filter_name:
            _append_google_feed_product_detail(details, "General", "Upholstery / Finish", joined_options)
        elif "colour" in filter_name or "color" in filter_name:
            if not _has_google_feed_detail_attribute(details, "Colour"):
                _append_google_feed_product_detail(details, "General", "Colour", joined_options)
        elif "material" in filter_name:
            if not _has_google_feed_detail_attribute(details, "Material"):
                _append_google_feed_product_detail(details, "General", "Material", joined_options)
        elif "shape" in filter_name:
            _append_google_feed_product_detail(details, "General", "Shape", joined_options)

    if extracted_color_text and not _has_google_feed_detail_attribute(details, "Colour"):
        _append_google_feed_product_detail(details, "General", "Colour", extracted_color_text)
    manual_dimension_fields = (
        ("Depth", "google_feed_depth"),
        ("Length", "google_feed_length"),
        ("Width", "google_feed_width"),
        ("Height", "google_feed_height"),
        ("Seat Height", "google_feed_seat_height"),
    )
    for attribute_name, field_name in manual_dimension_fields:
        _append_google_feed_product_detail(
            details,
            "Dimensions",
            attribute_name,
            _get_google_feed_manual_value(product, field_name),
        )

    for row in getattr(product, "dimensions", []) or []:
        if not isinstance(row, dict):
            continue
        measurement = str(row.get("measurement", "") or "").strip()
        normalised_measurement = _normalise_google_feed_detail_name(measurement)
        attribute_name = GOOGLE_FEED_DIMENSION_NAMES.get(normalised_measurement, measurement)
        if _has_google_feed_detail_attribute(details, attribute_name):
            continue
        attribute_value = _get_google_feed_dimension_value(row, size=size)
        _append_google_feed_product_detail(details, "Dimensions", attribute_name, attribute_value)

    for detail in _get_google_feed_dimension_paragraph_details(product):
        if _has_google_feed_detail_attribute(details, detail["attribute_name"]):
            continue
        _append_google_feed_product_detail(
            details,
            detail["section_name"],
            detail["attribute_name"],
            detail["attribute_value"],
        )

    return details


def _append_google_feed_product_detail_xml(lines: list, detail: dict) -> None:
    lines.extend([
        "      <g:product_detail>",
        f"        <g:section_name>{escape(detail['section_name'])}</g:section_name>",
        f"        <g:attribute_name>{escape(detail['attribute_name'])}</g:attribute_name>",
        f"        <g:attribute_value>{escape(detail['attribute_value'])}</g:attribute_value>",
        "      </g:product_detail>",
    ])


def _build_google_feed_item_xml(
    product,
    size=None,
    manual_variant: dict | None = None,
    manual_variant_index: int | None = None,
    frontend_base_url: str = "",
    backend_base_url: str = "",
    price_override: Decimal | None = None,
) -> str:
    """
    Build a single product item XML for Google Merchant feed.
    If size is provided, generates a variant entry with item_group_id.
    """
    product_link = urljoin(frontend_base_url, f"product/{product.slug}/")
    product_image = _get_google_feed_variant_image_url(product, manual_variant, backend_base_url)
    if not product_image:
        return ""
    description = (product.short_description or product.description or product.name or "").strip()
    availability = "in stock" if product.in_stock else "out of stock"
    brand = (_get_google_feed_manual_value(product, "google_feed_brand") or getattr(product.category, "name", "") or "Reve Living").strip()
    
    # ProductSize.price_delta stores the actual size price used by the storefront.
    variant_price = _get_google_feed_variant_price(manual_variant)
    price = Decimal(price_override) if price_override is not None else (variant_price if variant_price is not None else Decimal(product.price))
    if price_override is None and size and hasattr(size, "price_delta") and size.price_delta:
        price = Decimal(size.price_delta)
    if price <= 0:
        return ""
    price_text = f"{price.quantize(TWOPLACES)} GBP"
    
    # ID generation
    variant_sku = _get_google_feed_variant_value(manual_variant, "sku")
    variant_mpn = _get_google_feed_variant_value(manual_variant, "mpn")
    variant_gtin = _get_google_feed_variant_value(manual_variant, "gtin")
    base_sku = _get_google_feed_manual_value(product, "google_feed_sku")
    base_mpn = _get_google_feed_manual_value(product, "google_feed_mpn")
    base_gtin = _get_google_feed_manual_value(product, "google_feed_gtin")
    feed_sku = variant_sku or base_sku
    feed_mpn = variant_mpn or base_mpn
    feed_gtin = variant_gtin or base_gtin
    if manual_variant:
        item_id = variant_sku or f"{product.id}-v{manual_variant_index or 1}"
        mpn = feed_mpn or variant_sku or f"REVE-{product.id}-V{manual_variant_index or 1}"
        variant_title_parts = [
            _get_google_feed_variant_value(manual_variant, "color"),
            _get_google_feed_variant_value(manual_variant, "fabric"),
            _get_google_feed_variant_value(manual_variant, "size"),
        ]
        variant_title_suffix = " - ".join(part for part in variant_title_parts if part)
        title = f"{product.name} - {variant_title_suffix}" if variant_title_suffix else product.name
    elif size:
        item_id = f"{product.id}-{size.id}"
        mpn = feed_mpn or f"REVE-{product.id}-{size.id}"
        title = f"{product.name} - {size.name}"
    else:
        item_id = product.id
        mpn = feed_mpn or base_sku or f"REVE-{product.id}"
        title = product.name
    
    # Collect fabric/material info
    fabrics = getattr(product, "_prefetched_fabrics", [])
    filter_details = _get_google_feed_filter_details(product)
    materials = []
    if fabrics:
        materials = [f.name for f in fabrics if f.name]
    headboard_materials = materials[:]
    if not materials:
        materials = _get_google_feed_filter_values(filter_details, ("fabric", "material", "upholstery"))
    manual_material = _get_google_feed_manual_value(product, "google_feed_material")
    if manual_material:
        materials = [manual_material]
    material_text = ", ".join(materials) if materials else ""
    extra_attributes = _get_google_feed_extra_attributes(product, headboard_materials)
    
    # Collect color info
    colors = getattr(product, "_prefetched_colors", [])
    color_names = [c.name for c in colors if c.name]
    product_name_text = _normalise_google_feed_detail_name(getattr(product, "name", ""))
    product_text = _get_google_feed_product_text(product)
    filter_color_names = _get_google_feed_filter_values(filter_details, ("colour", "color"))
    title_color_names = _extract_google_feed_phrase_values(product_name_text, GOOGLE_FEED_COLOUR_PHRASES)
    extracted_color_names = _extract_google_feed_phrase_values(product_text, GOOGLE_FEED_COLOUR_PHRASES)
    manual_color = _get_google_feed_manual_value(product, "google_feed_color")
    variant_color = _get_google_feed_variant_value(manual_variant, "color")
    if variant_color:
        manual_color = variant_color
    if manual_color:
        title_color_names = [manual_color]
    color_names = title_color_names or color_names or filter_color_names or extracted_color_names
    manual_fabric_type = _get_google_feed_manual_value(product, "google_feed_fabric_type")
    variant_fabric = _get_google_feed_variant_value(manual_variant, "fabric")
    if variant_fabric:
        manual_fabric_type = variant_fabric
    extracted_fabric_names = [manual_fabric_type] if manual_fabric_type else ([] if material_text else _extract_google_feed_phrase_values(product_text, GOOGLE_FEED_FABRIC_TYPE_PHRASES))
    color_text = color_names[0] if color_names else ""
    detail_color_text = ", ".join(color_names) if color_names else ""
    extracted_fabric_text = ", ".join(extracted_fabric_names) if extracted_fabric_names else ""
    
    # Google Product Category
    subcategory_name = ""
    if hasattr(product, "subcategory") and product.subcategory:
        subcategory_name = product.subcategory.name
    google_category = _get_google_product_category(subcategory_name or brand)
    
    # Product Type (based on category/subcategory)
    product_type = f"{brand} > {subcategory_name}" if subcategory_name else brand
    product_details = _get_google_feed_product_details(
        product,
        size,
        material_text,
        detail_color_text,
        extra_attributes,
        extracted_color_text=", ".join(extracted_color_names) if extracted_color_names else "",
        extracted_fabric_text=extracted_fabric_text,
    )
    
    special_feature = _get_google_feed_manual_value(product, "google_feed_special_feature")
    has_storage = "storage" in special_feature.lower() or (
        not special_feature and hasattr(product, "description") and "storage" in product.description.lower()
    )
    
    lines = [
        "    <item>",
        f"      <g:id>{item_id}</g:id>",
        f"      <g:title>{escape(title)}</g:title>",
        f"      <g:description>{escape(description)}</g:description>",
        f"      <g:link>{escape(product_link)}</g:link>",
        f"      <g:image_link>{escape(product_image)}</g:image_link>",
        f"      <g:availability>{availability}</g:availability>",
        f"      <g:price>{price_text}</g:price>",
        "      <g:condition>new</g:condition>",
        f"      <g:brand>{escape(brand)}</g:brand>",
        f"      <g:mpn>{escape(mpn)}</g:mpn>",
        f"      <g:google_product_category>{escape(google_category)}</g:google_product_category>",
        f"      <g:product_type>{escape(product_type)}</g:product_type>",
    ]
    if feed_gtin:
        lines.append(f"      <g:gtin>{escape(feed_gtin)}</g:gtin>")
    
    # Add size if this is a variant
    if manual_variant:
        lines.append(f"      <g:item_group_id>{product.id}</g:item_group_id>")
    elif size:
        lines.append(f"      <g:item_group_id>{product.id}</g:item_group_id>")
        lines.append(f"      <g:size>{escape(size.name)}</g:size>")
    variant_size = _get_google_feed_variant_value(manual_variant, "size")
    if variant_size:
        lines.append(f"      <g:size>{escape(variant_size)}</g:size>")
    
    # Add material
    if material_text:
        lines.append(f"      <g:material>{escape(material_text)}</g:material>")
    
    if extra_attributes["frame_material"]:
        lines.append(f"      <g:frame_material>{escape(extra_attributes['frame_material'])}</g:frame_material>")
    if extra_attributes["headboard_material"]:
        lines.append(f"      <g:headboard_material>{escape(extra_attributes['headboard_material'])}</g:headboard_material>")
    if extra_attributes["number_of_drawers"]:
        lines.append(f"      <g:number_of_drawers>{escape(extra_attributes['number_of_drawers'])}</g:number_of_drawers>")
    
    for detail in product_details:
        _append_google_feed_product_detail_xml(lines, detail)

    age_range_detail = _get_google_feed_age_range_detail(product)
    if age_range_detail:
        _append_google_feed_product_detail_xml(lines, {
            "section_name": "General",
            "attribute_name": "Age Range",
            "attribute_value": age_range_detail,
        })

    if feed_sku:
        _append_google_feed_product_detail_xml(lines, {
            "section_name": "General",
            "attribute_name": "SKU",
            "attribute_value": feed_sku,
        })
    if feed_mpn:
        _append_google_feed_product_detail_xml(lines, {
            "section_name": "General",
            "attribute_name": "MPN",
            "attribute_value": feed_mpn,
        })
    if feed_gtin:
        _append_google_feed_product_detail_xml(lines, {
            "section_name": "General",
            "attribute_name": "GTIN",
            "attribute_value": feed_gtin,
        })

    if special_feature:
        _append_google_feed_product_detail_xml(lines, {
            "section_name": "General",
            "attribute_name": "Special Feature",
            "attribute_value": special_feature,
        })
    
    # Add color
    if color_text:
        lines.append(f"      <g:color>{escape(color_text)}</g:color>")
    
    # Add standard attributes for furniture
    lines.append(f"      <g:age_group>{_get_google_feed_age_group(product)}</g:age_group>")
    lines.append("      <g:gender>unisex</g:gender>")
    
    # Add custom label for products with storage
    if special_feature:
        lines.append(f"      <g:custom_label_0>{escape(special_feature)}</g:custom_label_0>")
    elif has_storage:
        lines.append("      <g:custom_label_0>Has Storage</g:custom_label_0>")
    
    lines.append("    </item>")
    return "\n".join(lines) + "\n"


def _build_google_feed_items_xml(product, frontend_base_url: str, backend_base_url: str) -> str:
    """
    Generate feed items for a product, creating separate entries for each size variant.
    """
    # Prefetch related objects
    sizes = list(product.sizes.all()) if hasattr(product, "sizes") else []
    colors = list(product.colors.all()) if hasattr(product, "colors") else []
    fabrics = list(product.fabrics.all()) if hasattr(product, "fabrics") else []
    images = list(product.images.all()) if hasattr(product, "images") else []
    filter_values = list(product.filter_values.all()) if hasattr(product, "filter_values") else []
    
    # Store prefetched data on product object for use in _build_google_feed_item_xml
    product._prefetched_colors = colors
    product._prefetched_fabrics = fabrics
    product._prefetched_images = images
    product._prefetched_filter_values = filter_values
    
    items_xml = ""
    manual_variants = _get_google_feed_manual_variants(product)
    if manual_variants:
        for index, variant in enumerate(manual_variants, start=1):
            items_xml += _build_google_feed_item_xml(
                product,
                manual_variant=variant,
                manual_variant_index=index,
                frontend_base_url=frontend_base_url,
                backend_base_url=backend_base_url,
            )
        return items_xml

    if _uses_lowest_google_feed_price(product):
        lowest_price = _get_lowest_google_feed_price(product, sizes)
        return _build_google_feed_item_xml(
            product,
            size=None,
            frontend_base_url=frontend_base_url,
            backend_base_url=backend_base_url,
            price_override=lowest_price,
        )
    
    # If product has sizes, create separate entries for each size
    if sizes:
        for size in sizes:
            items_xml += _build_google_feed_item_xml(product, size=size, frontend_base_url=frontend_base_url, backend_base_url=backend_base_url)
    else:
        # No sizes, create single entry
        items_xml += _build_google_feed_item_xml(product, size=None, frontend_base_url=frontend_base_url, backend_base_url=backend_base_url)
    
    return items_xml


def google_feed_xml(request):
    frontend_base_url = _normalize_base_url(
        getattr(settings, "FRONTEND_URL", ""),
        os.getenv("FRONTEND_URL", ""),
    )
    if not frontend_base_url:
        frontend_base_url = _normalize_base_url(request.build_absolute_uri("/"))

    backend_base_url = _normalize_base_url(
        getattr(settings, "BACKEND_URL", ""),
        request.build_absolute_uri("/"),
    )

    primary_image_subquery = (
        ProductImage.objects.filter(product_id=OuterRef("pk"))
        .exclude(url="")
        .order_by("sort_order", "id")
        .values("url")[:1]
    )
    products = (
        Product.objects.filter(is_hidden=False, imported_from_product__isnull=True, category__is_hidden=False)
        .filter(Q(subcategory__isnull=True) | Q(subcategory__is_hidden=False))
        .select_related("category", "subcategory")
        .prefetch_related("sizes", "colors", "fabrics", "images", "filter_values__filter_option__filter_type")
        .annotate(primary_image_url=Subquery(primary_image_subquery))
        .only("id", "name", "slug", "description", "short_description", "features", "dimensions", "dimension_paragraph", "dimension_note", "google_feed_brand", "google_feed_sku", "google_feed_mpn", "google_feed_gtin", "google_feed_special_feature", "google_feed_color", "google_feed_material", "google_feed_fabric_type", "google_feed_frame_material", "google_feed_headboard_material", "google_feed_number_of_drawers", "google_feed_depth", "google_feed_length", "google_feed_width", "google_feed_height", "google_feed_seat_height", "google_feed_variants", "price", "in_stock", "category__name", "category__slug", "category__is_hidden", "subcategory__name", "subcategory__slug", "subcategory__is_hidden")
        .order_by("id")
    )

    def _xml_stream():
        channel_link = backend_base_url.rstrip("/")
        yield '<?xml version="1.0" encoding="UTF-8"?>\n'
        yield '<rss version="2.0" xmlns:g="http://base.google.com/ns/1.0">\n'
        yield "  <channel>\n"
        yield "    <title>Reve Living Product Feed</title>\n"
        yield f"    <link>{escape(channel_link)}</link>\n"
        yield "    <description>Google Merchant Center product feed for Reve Living</description>\n"
        for product in products:
            yield _build_google_feed_items_xml(product, frontend_base_url, backend_base_url)
        yield "  </channel>\n"
        yield "</rss>\n"

    return StreamingHttpResponse(_xml_stream(), content_type="application/xml; charset=utf-8")


class HealthCheckView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"status": "ok"})


class AdminSummaryView(APIView):
    """
    Lightweight dashboard metrics for the admin panel.
    Returns totals plus month-over-month deltas.
    """

    permission_classes = [IsAdminUser]

    def get(self, request):
        from datetime import timedelta
        from django.db.models import Sum, Count

        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_end = start_of_month - timedelta(microseconds=1)
        prev_month_start = prev_month_end.replace(day=1)

        def pct_change(current, previous):
            if previous and previous != 0:
                return round(((current - previous) / previous) * 100, 2)
            return None

        # Orders and revenue
        from .models import Order, Product

        revenue_orders = Order.objects.filter(status__in=("paid", "shipped", "delivered")).exclude(refund_status="succeeded")
        orders_current = revenue_orders.filter(created_at__gte=start_of_month)
        orders_prev = revenue_orders.filter(created_at__gte=prev_month_start, created_at__lte=prev_month_end)

        total_revenue = float(
            revenue_orders.aggregate(total=Sum("total_amount"))["total"] or 0
        )
        revenue_current = float(orders_current.aggregate(total=Sum("total_amount"))["total"] or 0)
        revenue_prev = float(orders_prev.aggregate(total=Sum("total_amount"))["total"] or 0)

        total_orders = revenue_orders.count()

        # Kids Beds was reorganized from a parent-only category into
        # subcategories, creating linked placement copies. Count those linked
        # copies once while leaving every other category record untouched.
        kids_beds_scope = Q(category__slug="kids-beds") | Q(category__name__iexact="Kids Beds")
        kids_beds_products = Product.objects.filter(kids_beds_scope).values_list(
            "id", "imported_from_product_id"
        )
        unique_kids_beds_products = {
            imported_from_product_id or product_id
            for product_id, imported_from_product_id in kids_beds_products
        }
        total_products = Product.objects.exclude(kids_beds_scope).count() + len(unique_kids_beds_products)

        # Customers = non-staff, non-superuser accounts
        customers_qs = User.objects.filter(is_staff=False, is_superuser=False)
        total_customers = customers_qs.count()

        data = {
            "totals": {
                "revenue": total_revenue,
                "orders": total_orders,
                "products": total_products,
                "customers": total_customers,
            },
            "monthly": {
                "revenue": {
                    "current": revenue_current,
                    "previous": revenue_prev,
                    "change_percent": pct_change(revenue_current, revenue_prev),
                },
                "orders": {
                    "current": orders_current.count(),
                    "previous": orders_prev.count(),
                    "change_percent": pct_change(orders_current.count(), orders_prev.count()),
                },
            },
        }
        return Response(data)


class RegisterView(generics.CreateAPIView):
    queryset = User.objects.all()
    permission_classes = (AllowAny,)
    serializer_class = RegisterSerializer


class IsAdminOrReadOnly(IsAdminUser):
    def has_permission(self, request, view):
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return True
        return super().has_permission(request, view)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def _include_hidden(self):
        return bool(getattr(self.request.user, "is_staff", False) or self.request.headers.get("Authorization"))

    def _invalidate_cache(self):
        """Ensure category changes are reflected immediately on the site."""
        from django.core.cache import cache

        cache.clear()

    @method_decorator(cache_page(60 * 5))
    def _cached_list(self, request, *args, **kwargs):
        """Cache public category lists briefly to keep navbar/category pages snappy."""
        return super().list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        """
        Keep admin category management fully fresh.
        Staff/admin requests should never wait behind a stale 5-minute cache window.
        """
        has_auth_header = bool(request.headers.get("Authorization"))
        if getattr(request.user, "is_staff", False) or has_auth_header:
            return super().list(request, *args, **kwargs)
        return self._cached_list(request, *args, **kwargs)

    def get_queryset(self):
        subcategory_queryset = SubCategory.objects.select_related("category").prefetch_related(
            Prefetch(
                "additional_categories",
                queryset=Category.objects.only("id", "name", "slug"),
                to_attr="_prefetched_additional_categories",
            )
        )
        if not self._include_hidden():
            subcategory_queryset = subcategory_queryset.filter(is_hidden=False)

        queryset = Category.objects.all()
        if not self._include_hidden():
            queryset = queryset.filter(is_hidden=False)
        queryset = queryset.prefetch_related(
            Prefetch(
                "subcategories",
                queryset=subcategory_queryset.order_by("sort_order", "name"),
                to_attr="prefetched_primary_subcategories",
            ),
            Prefetch(
                "shared_subcategories",
                queryset=subcategory_queryset.order_by("sort_order", "name"),
                to_attr="prefetched_shared_subcategories",
            ),
        ).order_by("sort_order", "name")
        slug = self.request.query_params.get("slug")
        if slug:
            queryset = queryset.filter(slug=slug)
        return queryset

    def perform_create(self, serializer):
        slug = serializer.validated_data.get("slug") or slugify(serializer.validated_data.get("name", ""))
        serializer.save(slug=slug)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def partial_update(self, request, *args, **kwargs):
        response = super().partial_update(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def destroy(self, request, *args, **kwargs):
        response = super().destroy(request, *args, **kwargs)
        self._invalidate_cache()
        return response


class SubCategoryViewSet(viewsets.ModelViewSet):
    queryset = SubCategory.objects.all().select_related("category").prefetch_related(
        Prefetch(
            "additional_categories",
            queryset=Category.objects.only("id", "name", "slug"),
            to_attr="_prefetched_additional_categories",
        )
    ).order_by("sort_order", "name")
    serializer_class = SubCategorySerializer
    permission_classes = [IsAdminOrReadOnly]

    def _include_hidden(self):
        return bool(getattr(self.request.user, "is_staff", False) or self.request.headers.get("Authorization"))

    def _invalidate_cache(self):
        """Ensure category listings reflect subcategory changes immediately."""
        from django.core.cache import cache

        cache.clear()

    @method_decorator(cache_page(60 * 5))
    def _cached_list(self, request, *args, **kwargs):
        """Cache public subcategory lists briefly for category navigation."""
        return super().list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        """
        Keep admin subcategory management fresh while allowing public navigation
        requests to benefit from a short cache window.
        """
        has_auth_header = bool(request.headers.get("Authorization"))
        if getattr(request.user, "is_staff", False) or has_auth_header:
            return super().list(request, *args, **kwargs)
        return self._cached_list(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if not self._include_hidden():
            queryset = queryset.filter(is_hidden=False, category__is_hidden=False)
        category_id = self.request.query_params.get("category")
        if category_id:
            queryset = queryset.filter(Q(category_id=category_id) | Q(additional_categories__id=category_id)).distinct()
        return queryset

    def perform_create(self, serializer):
        slug = serializer.validated_data.get("slug") or slugify(serializer.validated_data.get("name", ""))
        serializer.save(slug=slug)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def partial_update(self, request, *args, **kwargs):
        response = super().partial_update(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def destroy(self, request, *args, **kwargs):
        response = super().destroy(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="unlink-category")
    def unlink_category(self, request, pk=None):
        subcategory = self.get_object()
        category_id = request.data.get("category")

        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            raise ValidationError({"category": "A valid category ID is required."})

        linked_ids = subcategory.linked_category_ids()
        if category_id not in linked_ids:
            raise ValidationError({"category": "This subcategory is not linked to that category."})
        if len(linked_ids) <= 1:
            raise ValidationError({"category": "You must keep at least one category linked to this subcategory."})

        with transaction.atomic():
            if subcategory.category_id == category_id:
                replacement_id = (
                    subcategory.additional_categories.exclude(id=category_id)
                    .order_by("id")
                    .values_list("id", flat=True)
                    .first()
                )
                if not replacement_id:
                    raise ValidationError({"category": "No replacement category is available."})
                subcategory.additional_categories.remove(replacement_id)
                subcategory.category_id = replacement_id
                subcategory.save(update_fields=["category"])
            else:
                subcategory.additional_categories.remove(category_id)

        subcategory.refresh_from_db()
        self._invalidate_cache()
        serializer = self.get_serializer(subcategory)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="promote-to-category")
    def promote_to_category(self, request, pk=None):
        subcategory = self.get_object()

        with transaction.atomic():
            category_payload = {
                "name": subcategory.name,
                "slug": subcategory.slug,
                "description": subcategory.description,
                "image": subcategory.image,
                "is_hidden": subcategory.is_hidden,
                "show_in_collections": subcategory.show_in_collections,
                "show_in_all_collections": subcategory.show_in_all_collections,
                "image_alt_text": subcategory.image_alt_text,
                "meta_title": subcategory.meta_title,
                "meta_description": subcategory.meta_description,
                "sort_order": subcategory.sort_order,
            }
            category_serializer = CategorySerializer(data=category_payload, context=self.get_serializer_context())
            category_serializer.is_valid(raise_exception=True)
            promoted_category = category_serializer.save()

            product_count = Product.objects.filter(subcategory_id=subcategory.id).update(
                category=promoted_category,
                subcategory=None,
            )

            filter_count = CategoryFilter.objects.filter(subcategory_id=subcategory.id).update(
                category=promoted_category,
                subcategory=None,
            )

            promotion_count = 0
            for promotion in Promotion.objects.filter(subcategories=subcategory).distinct():
                promotion.categories.add(promoted_category)
                promotion.subcategories.remove(subcategory)
                promotion_count += 1

            mattress_count = 0
            for mattress in MattressOption.objects.filter(subcategories=subcategory).distinct():
                mattress.categories.add(promoted_category)
                mattress.subcategories.remove(subcategory)
                mattress_count += 1

            hero_slide_count = HeroSlide.objects.filter(subcategory_id=subcategory.id).update(
                category=promoted_category,
                subcategory=None,
                cta_link=f"/category/{promoted_category.slug}",
            )
            HeroSlide.objects.filter(selected_subcategories=subcategory).update(category=promoted_category)
            HeroSlide.selected_subcategories.through.objects.filter(subcategory_id=subcategory.id).delete()

            promoted_name = subcategory.name
            subcategory.delete()

        self._invalidate_cache()
        return Response(
            {
                "category": CategorySerializer(promoted_category, context=self.get_serializer_context()).data,
                "migrated": {
                    "products": product_count,
                    "category_filters": filter_count,
                    "promotions": promotion_count,
                    "mattress_options": mattress_count,
                    "hero_slides": hero_slide_count,
                },
                "message": f"{promoted_name} was promoted to a main category.",
            },
            status=status.HTTP_200_OK,
        )


class CollectionViewSet(viewsets.ModelViewSet):
    queryset = (
        Collection.objects.all()
        .prefetch_related(
            Prefetch(
                "products",
                queryset=_with_live_review_summary(
                    Product.objects.select_related("category", "subcategory")
                    .only(
                        "id",
                        "name",
                        "slug",
                        "meta_title",
                        "meta_description",
                        "category_id",
                        "subcategory_id",
                        "price",
                        "original_price",
                        "discount_percentage",
                        "stock_status",
                        "in_stock",
                        "is_hidden",
                        "is_bestseller",
                        "is_new",
                        "show_size_icons",
                        "rating",
                        "review_count",
                        "dimension_paragraph",
                        "dimension_note",
                        "show_dimensions_table",
                        "sort_order",
                        "assembly_service_enabled",
                        "assembly_service_price",
                        "short_description",
                        "created_at",
                        "category__name",
                        "category__slug",
                        "category__discount_override_enabled",
                        "category__discount_percentage",
                        "subcategory__name",
                        "subcategory__slug",
                        "subcategory__discount_override_enabled",
                        "subcategory__discount_percentage",
                    )
                    .prefetch_related(
                        "images",
                        "sizes",
                        Prefetch(
                            "filter_values",
                            queryset=ProductFilterValue.objects.select_related("filter_option__filter_type"),
                            to_attr="filter_values_all",
                        ),
                    )
                    .order_by("sort_order", "-created_at")
                )
            )
        )
        .order_by("sort_order", "name")
    )
    serializer_class = CollectionSerializer
    permission_classes = [IsAdminOrReadOnly]

    def _invalidate_cache(self):
        """Prevent stale collection lists after admin changes."""
        from django.core.cache import cache

        cache.clear()

    @method_decorator(cache_page(60 * 5))
    def list(self, request, *args, **kwargs):
        """Cache collection list for 5 minutes to prevent repeated heavy DB hits."""
        return super().list(request, *args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        slug = self.request.query_params.get("slug")
        if slug:
            queryset = queryset.filter(slug=slug)
        return queryset

    def perform_create(self, serializer):
        slug = serializer.validated_data.get("slug") or slugify(serializer.validated_data.get("name", ""))
        serializer.save(slug=slug)

    def create(self, request, *args, **kwargs):
        response = super().create(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def partial_update(self, request, *args, **kwargs):
        response = super().partial_update(request, *args, **kwargs)
        self._invalidate_cache()
        return response

    def destroy(self, request, *args, **kwargs):
        response = super().destroy(request, *args, **kwargs)
        self._invalidate_cache()
        return response


class HeroSlideViewSet(viewsets.ModelViewSet):
    queryset = HeroSlide.objects.all().select_related(
        "category", "subcategory", "subcategory__category"
    ).prefetch_related("selected_subcategories").order_by("sort_order", "-updated_at")
    serializer_class = HeroSlideSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        active_only = self.request.query_params.get("active_only")
        if not self.request.user.is_staff:
            queryset = queryset.filter(is_active=True)
        elif active_only in ("1", "true", "True"):
            queryset = queryset.filter(is_active=True)
        return queryset

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()


class LifestyleSectionViewSet(viewsets.ModelViewSet):
    queryset = LifestyleSection.objects.all().prefetch_related("articles").order_by("-updated_at", "-id")
    serializer_class = LifestyleSectionSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        active_only = self.request.query_params.get("active_only")
        if not getattr(self.request.user, "is_staff", False):
            queryset = queryset.filter(is_active=True)
        elif active_only in ("1", "true", "True"):
            queryset = queryset.filter(is_active=True)
        return queryset


class LifestyleArticleViewSet(viewsets.ModelViewSet):
    queryset = LifestyleArticle.objects.select_related("section").order_by("sort_order", "-updated_at", "-id")
    serializer_class = LifestyleArticleSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        section_id = self.request.query_params.get("section")
        slug = self.request.query_params.get("slug")
        active_only = self.request.query_params.get("active_only")
        if section_id:
            queryset = queryset.filter(section_id=section_id)
        if slug:
            queryset = queryset.filter(slug=slug)
        if not getattr(self.request.user, "is_staff", False):
            queryset = queryset.filter(is_active=True, section__is_active=True)
        elif active_only in ("1", "true", "True"):
            queryset = queryset.filter(is_active=True, section__is_active=True)
        return queryset


class ProductViewSet(viewsets.ModelViewSet):
    """
    Product listing/detail.
    - Uses a lightweight queryset for list responses to cut payload size & query count.
    - Falls back to a fully-prefetched queryset for detail/admin mutations.
    - Caches list responses briefly to smooth category page load times.
    """

    # Base queryset needed for DRF router basename resolution
    queryset = Product.objects.all()
    permission_classes = [IsAdminOrReadOnly]
    _non_filter_query_params = {
        "summary",
        "admin_summary",
        "category",
        "subcategory",
        "bestseller",
        "is_new",
        "slug",
        "limit",
        "offset",
        "page",
        "page_size",
        "include_total",
        "include_filters",
        "include_sizes",
        "include_variants",
        "include_content",
        "card",
        "quick",
        "core",
        "admin_detail",
        "format",
        "ordering",
        "search",
        "q",
        "admin_picker",
    }

    # Prefetch groups tuned for list vs detail
    _size_list_prefetch = Prefetch(
        "sizes",
        queryset=ProductSize.objects.only("id", "product_id", "name", "description", "price_delta", "stock_status").order_by("id"),
    )
    _filter_values_list_prefetch = Prefetch(
        "filter_values",
        queryset=(
            ProductFilterValue.objects.select_related("filter_option__filter_type")
            .only(
                "id",
                "product_id",
                "filter_option_id",
                "filter_option__id",
                "filter_option__slug",
                "filter_option__filter_type__id",
                "filter_option__filter_type__slug",
            )
        ),
        to_attr="filter_values_all",
    )
    _summary_color_prefetch = Prefetch(
        "colors",
        queryset=ProductColor.objects.only("id", "product_id", "name", "hex_code", "is_available", "stock_status").order_by("id"),
    )
    _summary_style_prefetch = Prefetch(
        "styles",
        queryset=ProductStyle.objects.only("id", "product_id", "name", "options").order_by("id"),
    )
    _summary_base_prefetches = []
    _summary_filter_prefetches = [_filter_values_list_prefetch]
    _summary_variant_prefetches = [_summary_color_prefetch, _summary_style_prefetch]
    _list_prefetches = [_size_list_prefetch, _filter_values_list_prefetch]
    _core_detail_prefetches = [
        "images",
        _size_list_prefetch,
        "colors",
        "styles",
        "fabrics",
        "mattresses",
        "dimension_template_link__template__rows",
    ]
    _detail_prefetches = ["images"] + _list_prefetches + [
        "videos",
        "colors",
        "styles",
        "fabrics",
        "mattresses",
        "dimension_template_link__template__rows",
        Prefetch(
            "suggested_products",
            queryset=_with_live_review_summary(
                Product.objects.filter(is_hidden=False)
                .select_related("category", "subcategory")
                .prefetch_related("images", "sizes")
                .order_by("sort_order", "-created_at")
            ),
            to_attr="prefetched_suggested_products",
        ),
    ]
    _admin_detail_prefetches = [
        "images",
        "videos",
        _size_list_prefetch,
        _filter_values_list_prefetch,
        "colors",
        "styles",
        "fabrics",
        Prefetch(
            "mattresses",
            queryset=ProductMattress.objects.only(
                "id",
                "product_id",
                "source_product_id",
                "name",
                "description",
                "image_url",
                "price",
                "enable_bunk_positions",
                "price_top",
                "price_bottom",
                "price_both",
                "is_hidden",
            ),
        ),
        Prefetch("suggested_products", queryset=Product.objects.only("id")),
    ]
    _list_only_fields = [
        "id",
        "name",
        "slug",
        "meta_title",
        "meta_description",
        "category_id",
        "subcategory_id",
        "imported_from_product_id",
        "price",
        "original_price",
        "discount_percentage",
        "stock_status",
        "in_stock",
        "is_hidden",
        "is_bestseller",
        "is_new",
        "show_size_icons",
        "rating",
        "review_count",
        "dimension_paragraph",
        "dimension_note",
        "show_dimensions_table",
        "sort_order",
        "assembly_service_enabled",
        "assembly_service_price",
        "short_description",
        "created_at",
    ]
    _summary_only_fields = [
        "id",
        "name",
        "slug",
        "category_id",
        "subcategory_id",
        "imported_from_product_id",
        "price",
        "original_price",
        "discount_percentage",
        "short_description",
        "stock_status",
        "in_stock",
        "is_hidden",
        "is_bestseller",
        "is_new",
        "sort_order",
        "created_at",
    ]
    _admin_list_only_fields = [
        "id",
        "name",
        "slug",
        "category_id",
        "subcategory_id",
        "imported_from_product_id",
        "price",
        "original_price",
        "stock_status",
        "in_stock",
        "is_hidden",
        "is_bestseller",
        "is_new",
        "sort_order",
    ]
    _admin_picker_only_fields = [
        "id",
        "name",
        "slug",
        "category_id",
        "subcategory_id",
    ]

    def _base_queryset(self):
        return Product.objects.select_related("category", "subcategory")

    @action(detail=False, methods=["get"], permission_classes=[AllowAny], url_path="seo")
    def seo(self, request):
        """Compact product metadata used to generate crawlable product HTML at build time."""
        products = list(
            _with_live_review_summary(
                Product.objects.filter(
                    is_hidden=False,
                    category__is_hidden=False,
                )
                .filter(Q(subcategory__isnull=True) | Q(subcategory__is_hidden=False))
                .annotate(primary_image_url=Subquery(_primary_image_subquery()))
            )
            .values(
                "id",
                "name",
                "slug",
                "meta_title",
                "meta_description",
                "short_description",
                "description",
                "price",
                "delivery_charges",
                "stock_status",
                "in_stock",
                "primary_image_url",
                "live_rating",
                "live_review_count",
            )
            .order_by("id")
        )
        product_ids = [product["id"] for product in products]
        reviews_by_product = {}
        for review in (
            Review.objects.filter(
                product_id__in=product_ids,
                is_visible=True,
            )
            .exclude(comment="")
            .order_by("product_id", "-created_at")
            .values("product_id", "name", "rating", "comment", "created_at")
        ):
            bucket = reviews_by_product.setdefault(review["product_id"], [])
            if len(bucket) >= 10:
                continue
            bucket.append(
                {
                    "name": review["name"],
                    "rating": review["rating"],
                    "comment": review["comment"],
                    "created_at": review["created_at"].isoformat() if review["created_at"] else None,
                }
            )

        backend_base_url = request.build_absolute_uri("/")
        data = []
        for product in products:
            stock_status = Product.normalize_stock_status(
                product["stock_status"],
                fallback_in_stock=bool(product["in_stock"]),
            )
            product_payload = {
                key: value
                for key, value in product.items()
                if key not in ("live_rating", "live_review_count")
            }
            data.append(
                {
                    **product_payload,
                    "price": str(product["price"]),
                    "delivery_charges": str(product["delivery_charges"]),
                    "rating": round(float(product["live_rating"] or 0), 1),
                    "review_count": int(product["live_review_count"] or 0),
                    "reviews": reviews_by_product.get(product["id"], []),
                    "stock_status": stock_status,
                    "primary_image_url": _to_absolute_url(
                        product["primary_image_url"],
                        backend_base_url,
                    ),
                }
            )
        return Response(data)

    def _get_category_scope_ids(self, category_slug):
        cache_key = f"product-category-scope:v1:{category_slug}"
        if _has_usable_cache_backend():
            cached = cache.get(cache_key)
            if cached is not None:
                return cached

        category_id = Category.objects.filter(slug=category_slug).values_list("id", flat=True).first()
        if not category_id:
            result = (None, [])
        else:
            linked_subcategory_ids = list(
                SubCategory.objects.filter(
                    Q(category_id=category_id) | Q(additional_categories__id=category_id)
                )
                .values_list("id", flat=True)
                .distinct()
            )
            result = (category_id, linked_subcategory_ids)

        if _has_usable_cache_backend():
            cache.set(cache_key, result, CATEGORY_FILTER_CACHE_TTL)
        return result

    def _is_admin_summary_request(self):
        return (
            self.action == "list"
            and self.request.query_params.get("admin_summary") in ("1", "true", "True")
        )

    def _is_admin_picker_request(self):
        return (
            self.action == "list"
            and self.request.query_params.get("admin_picker") in ("1", "true", "True")
        )

    def _summary_includes_filters(self):
        return (
            self.action == "list"
            and self.request.query_params.get("summary") in ("1", "true", "True")
            and self.request.query_params.get("include_filters") in ("1", "true", "True")
        )

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["include_product_filter_values"] = self._summary_includes_filters()
        return context

    def _summary_includes_variants(self):
        return (
            self.action == "list"
            and self.request.query_params.get("summary") in ("1", "true", "True")
            and self.request.query_params.get("include_variants") in ("1", "true", "True")
        )

    def _summary_includes_sizes(self):
        return (
            self.action == "list"
            and self.request.query_params.get("summary") in ("1", "true", "True")
            and self.request.query_params.get("include_sizes") in ("1", "true", "True")
        )

    def _summary_includes_content(self):
        return (
            self.action == "list"
            and self.request.query_params.get("summary") in ("1", "true", "True")
            and self.request.query_params.get("include_content") in ("1", "true", "True")
        )

    def _summary_includes_total(self):
        return (
            self.action == "list"
            and self.request.query_params.get("summary") in ("1", "true", "True")
            and self.request.query_params.get("include_total") in ("1", "true", "True")
        )

    def _is_core_detail_request(self):
        return self.request.query_params.get("core") in ("1", "true", "True")

    def _is_quick_detail_request(self):
        return self.request.query_params.get("quick") in ("1", "true", "True")

    def _is_admin_detail_request(self):
        return (
            self.action == "retrieve"
            and self.request.query_params.get("admin_detail") in ("1", "true", "True")
            and self.request.user
            and self.request.user.is_authenticated
            and self.request.user.is_staff
        )

    def get_serializer_class(self):
        if self._is_quick_detail_request():
            return ProductQuickSerializer
        if self._is_admin_detail_request():
            return ProductAdminDetailSerializer
        if self._is_admin_picker_request():
            return ProductAdminPickerSerializer
        if self._is_admin_summary_request():
            return ProductAdminListSerializer
        if self.action == "list" and self.request.query_params.get("summary") in ("1", "true", "True"):
            return ProductSummarySerializer
        if self.action == "list" and not self.request.query_params.get("slug"):
            from .serializers import ProductListSerializer
            return ProductListSerializer
        if self.request.method in ("POST", "PUT", "PATCH"):
            return ProductWriteSerializer
        return ProductSerializer

    def get_queryset(self):
        # Choose a lighter prefetch set for list views (most traffic)
        is_list = self.action == "list" and not self.request.query_params.get("slug")
        is_admin_picker = self._is_admin_picker_request()
        is_admin_summary = self._is_admin_summary_request()
        is_summary = is_list and self.request.query_params.get("summary") in ("1", "true", "True")
        primary_image_subquery = _primary_image_subquery()
        primary_image_flip_subquery = _primary_image_flip_subquery()
        min_size_price_subquery = _min_size_price_subquery()
        size_count_subquery = _size_count_subquery()
        if is_admin_picker:
            queryset = (
                Product.objects.select_related("category", "subcategory")
                .only(
                    *self._admin_picker_only_fields,
                    "category__name",
                    "category__slug",
                    "category__discount_override_enabled",
                    "category__discount_percentage",
                    "subcategory__name",
                    "subcategory__slug",
                    "subcategory__discount_override_enabled",
                    "subcategory__discount_percentage",
                )
            )
        elif is_admin_summary:
            queryset = (
                Product.objects.select_related("category", "subcategory")
                .only(
                    *self._admin_list_only_fields,
                    "category__name",
                    "category__slug",
                    "subcategory__name",
                    "subcategory__slug",
                )
            )
        elif is_summary:
            summary_prefetches = list(self._summary_base_prefetches)
            if self._summary_includes_sizes():
                summary_prefetches.append(self._size_list_prefetch)
            if self._summary_includes_filters():
                summary_prefetches.extend(self._summary_filter_prefetches)
            if self._summary_includes_variants():
                summary_prefetches.extend(self._summary_variant_prefetches)
            summary_only_fields = list(self._summary_only_fields)
            if self._summary_includes_content():
                summary_only_fields.extend(
                    [
                        "description",
                        "features",
                        "dimensions",
                        "dimension_paragraph",
                        "dimension_note",
                        "dimension_images",
                        "show_dimensions_table",
                    ]
                )
            queryset = (
                self._base_queryset()
                .prefetch_related(*summary_prefetches)
                .annotate(
                    primary_image_url=Subquery(primary_image_subquery),
                    primary_image_flip_horizontal=Subquery(primary_image_flip_subquery, output_field=BooleanField()),
                    min_size_price=Subquery(min_size_price_subquery, output_field=DecimalField(max_digits=10, decimal_places=2)),
                    size_count=Subquery(size_count_subquery, output_field=IntegerField()),
                )
                .only(
                    *summary_only_fields,
                    "category__name",
                    "category__slug",
                    "category__discount_override_enabled",
                    "category__discount_percentage",
                    "subcategory__name",
                    "subcategory__slug",
                    "subcategory__discount_override_enabled",
                    "subcategory__discount_percentage",
                )
            )
        else:
            if self._is_quick_detail_request():
                prefetches = []
            else:
                prefetches = self._list_prefetches if is_list else (
                    self._admin_detail_prefetches if self._is_admin_detail_request() else (
                        self._core_detail_prefetches if self._is_core_detail_request() else self._detail_prefetches
                    )
                )
            queryset = self._base_queryset().prefetch_related(*prefetches)
            if self._is_quick_detail_request():
                queryset = queryset.annotate(
                    primary_image_url=Subquery(primary_image_subquery),
                    primary_image_flip_horizontal=Subquery(primary_image_flip_subquery, output_field=BooleanField()),
                )
            if is_list:
                queryset = (
                    queryset.annotate(
                        primary_image_url=Subquery(primary_image_subquery),
                        primary_image_flip_horizontal=Subquery(primary_image_flip_subquery, output_field=BooleanField()),
                    )
                    .only(
                        *self._list_only_fields,
                        "category__name",
                        "category__slug",
                        "category__discount_override_enabled",
                        "category__discount_percentage",
                        "subcategory__name",
                        "subcategory__slug",
                        "subcategory__discount_override_enabled",
                        "subcategory__discount_percentage",
                    )
                )
        is_admin_request = bool(self.request.user and self.request.user.is_authenticated and self.request.user.is_staff)
        if not is_admin_request:
            queryset = queryset.filter(is_hidden=False, category__is_hidden=False).filter(
                Q(subcategory__isnull=True) | Q(subcategory__is_hidden=False)
            )

        category = self.request.query_params.get("category")
        subcategory = self.request.query_params.get("subcategory")
        bestseller = self.request.query_params.get("bestseller")
        is_new = self.request.query_params.get("is_new")
        slug = self.request.query_params.get("slug")
        search = (self.request.query_params.get("search") or self.request.query_params.get("q") or "").strip()
        
        if category:
            category_id, linked_subcategory_ids = self._get_category_scope_ids(category)
            if not category_id:
                return queryset.none()

            category_filter = Q(category_id=category_id)
            if linked_subcategory_ids:
                category_filter |= Q(subcategory_id__in=linked_subcategory_ids)
            queryset = queryset.filter(category_filter)
        if subcategory:
            queryset = queryset.filter(subcategory__slug=subcategory)
        if bestseller:
            queryset = queryset.filter(is_bestseller=True)
        if is_new:
            queryset = queryset.filter(is_new=True)
        if slug:
            queryset = queryset.filter(slug=slug)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(slug__icontains=search)
                | Q(category__name__icontains=search)
                | Q(category__slug__icontains=search)
                | Q(subcategory__name__icontains=search)
                | Q(subcategory__slug__icontains=search)
            )
        
        # Apply dynamic filters with EXISTS so category listings do not pay for
        # joined rows + DISTINCT on every filter click.
        filter_param_keys = set(self.request.query_params.keys()) - self._non_filter_query_params
        if filter_param_keys:
            requested_filter_values = {
                key: [
                    value.strip()
                    for value in (self.request.query_params.get(key) or "").split(",")
                    if value.strip()
                ]
                for key in filter_param_keys
            }
            requested_filter_values = {
                key: values for key, values in requested_filter_values.items() if values
            }
            if requested_filter_values:
                active_filter_slugs = set(
                    FilterType.objects.filter(
                        is_active=True,
                        slug__in=requested_filter_values.keys(),
                    ).values_list("slug", flat=True)
                )
                requested_filter_values = {
                    key: values
                    for key, values in requested_filter_values.items()
                    if key in active_filter_slugs
                }
            if requested_filter_values:
                option_ids_by_filter = {}
                option_rows = (
                    FilterOption.objects.filter(
                        filter_type__is_active=True,
                        filter_type__slug__in=requested_filter_values.keys(),
                    )
                    .filter(
                        Q(
                            *[
                                Q(filter_type__slug=filter_slug, slug__in=option_slugs)
                                for filter_slug, option_slugs in requested_filter_values.items()
                            ],
                            _connector=Q.OR,
                        )
                    )
                    .values_list("filter_type__slug", "id")
                )
                for filter_slug, option_id in option_rows:
                    option_ids_by_filter.setdefault(filter_slug, []).append(option_id)

                for filter_slug in requested_filter_values:
                    option_ids = option_ids_by_filter.get(filter_slug)
                    if not option_ids:
                        return queryset.none()
                    queryset = queryset.filter(
                        Exists(
                            ProductFilterValue.objects.filter(
                                product_id=OuterRef("pk"),
                                filter_option_id__in=option_ids,
                            )
                        )
                    )
        
        if category and not subcategory:
            queryset = queryset.order_by("subcategory__sort_order", "subcategory__name", "sort_order", "-created_at")
        else:
            queryset = queryset.order_by("sort_order", "-created_at")

        if self.request.method in ("GET", "HEAD", "OPTIONS") and not is_admin_picker and not is_admin_summary:
            queryset = _with_live_review_summary(queryset)

        return queryset

    def _product_list_cache_key(self, params):
        return f"product-list:v8:{urlencode(sorted(params.items()), doseq=True)}"

    def _summary_queryset_for_cache(self):
        primary_image_subquery = _primary_image_subquery()
        primary_image_flip_subquery = _primary_image_flip_subquery()
        min_size_price_subquery = _min_size_price_subquery()
        size_count_subquery = _size_count_subquery()
        return _with_live_review_summary(
            Product.objects.select_related("category", "subcategory")
            .filter(is_hidden=False, category__is_hidden=False)
            .filter(Q(subcategory__isnull=True) | Q(subcategory__is_hidden=False))
            .annotate(
                primary_image_url=Subquery(primary_image_subquery),
                primary_image_flip_horizontal=Subquery(primary_image_flip_subquery, output_field=BooleanField()),
                min_size_price=Subquery(min_size_price_subquery, output_field=DecimalField(max_digits=10, decimal_places=2)),
                size_count=Subquery(size_count_subquery, output_field=IntegerField()),
            )
            .only(
                *self._summary_only_fields,
                "category__name",
                "category__slug",
                "category__discount_override_enabled",
                "category__discount_percentage",
                "subcategory__name",
                "subcategory__slug",
                "subcategory__discount_override_enabled",
                "subcategory__discount_percentage",
            )
        )

    def _cache_first_summary_page(self, params):
        if not _has_usable_cache_backend() or PRODUCT_LIST_CACHE_TTL <= 0:
            return

        queryset = self._summary_queryset_for_cache()
        category_slug = params.get("category")
        subcategory_slug = params.get("subcategory")

        if category_slug:
            category_id, linked_subcategory_ids = self._get_category_scope_ids(category_slug)
            if not category_id:
                return
            category_filter = Q(category_id=category_id)
            if linked_subcategory_ids:
                category_filter |= Q(subcategory_id__in=linked_subcategory_ids)
            queryset = queryset.filter(category_filter).order_by(
                "subcategory__sort_order",
                "subcategory__name",
                "sort_order",
                "-created_at",
            )
        elif subcategory_slug:
            queryset = queryset.filter(subcategory__slug=subcategory_slug).order_by("sort_order", "-created_at")
        else:
            return

        limit = int(params.get("limit") or 18)
        total = queryset.count()
        page_items = list(queryset[:limit])
        data = ProductSummarySerializer(
            page_items,
            many=True,
            context={"include_product_filter_values": False},
        ).data
        payload = {"count": total, "results": data} if params.get("include_total") == "1" else data
        cache.set(self._product_list_cache_key(params), payload, PRODUCT_LIST_CACHE_TTL)

    def _prewarm_product_cache_for_product(self, product_id):
        if not product_id or not _has_usable_cache_backend():
            return
        try:
            product = Product.objects.select_related("category", "subcategory").get(pk=product_id)
        except Product.DoesNotExist:
            return

        scopes = []
        if product.category_id and product.category and product.category.slug:
            scopes.append({"category": product.category.slug})
        if product.subcategory_id and product.subcategory and product.subcategory.slug:
            scopes.append({"subcategory": product.subcategory.slug})

        for scope in scopes:
            params = {"summary": "1", "limit": "18", "include_total": "1", **scope}
            try:
                self._cache_first_summary_page(params)
            except Exception:
                logger.exception("Failed to prewarm product list cache for scope=%s", scope)

    def _schedule_product_cache_prewarm(self, product_id):
        if not product_id or not _has_usable_cache_backend():
            return

        def run():
            self._prewarm_product_cache_for_product(product_id)

        threading.Thread(target=run, daemon=True).start()

    def _invalidate_cache(self, product=None):
        """Refresh affected storefront product-list caches after admin changes."""
        if not _has_usable_cache_backend():
            return
        cache.clear()
        if product is not None:
            self._schedule_product_cache_prewarm(getattr(product, "pk", None))

    def list(self, request, *args, **kwargs):
        """
        Cache anonymous storefront product lists briefly. Admin mutations clear
        the process cache, and the short TTL smooths over cold Render/Neon waits.
        """
        should_profile = PRODUCT_SQL_DEBUG_LOG
        previous_force_debug_cursor = connection.force_debug_cursor
        if should_profile:
            connection.force_debug_cursor = True
        query_start = len(connection.queries) if should_profile else 0
        request_start = time.perf_counter()
        timings = {
            "queryset_ms": 0.0,
            "count_ms": 0.0,
            "db_fetch_ms": 0.0,
            "serializer_ms": 0.0,
            "filters_generation_ms": 0.0,
        }
        limit = _positive_int_query_param(request, "limit", maximum=100)
        has_page_param = request.query_params.get("page") or request.query_params.get("page_size")
        is_summary = request.query_params.get("summary") in ("1", "true", "True")
        is_limited_summary = (
            limit is not None
            and is_summary
            and not self._is_admin_summary_request()
        )
        is_page_summary = bool(
            has_page_param
            and is_summary
            and not self._is_admin_summary_request()
        )
        include_total = is_limited_summary and self._summary_includes_total()

        def limited_summary_response():
            queryset_start = time.perf_counter()
            queryset = self.filter_queryset(self.get_queryset())
            timings["queryset_ms"] += (time.perf_counter() - queryset_start) * 1000
            offset = _nonnegative_int_query_param(request, "offset")
            if include_total:
                count_start = time.perf_counter()
                total = queryset.count()
                timings["count_ms"] += (time.perf_counter() - count_start) * 1000
                fetch_start = time.perf_counter()
                page_items = list(queryset[offset : offset + limit])
                timings["db_fetch_ms"] += (time.perf_counter() - fetch_start) * 1000
                serializer_start = time.perf_counter()
                serializer = self.get_serializer(page_items, many=True)
                data = serializer.data
                timings["serializer_ms"] += (time.perf_counter() - serializer_start) * 1000
                return Response({"count": total, "results": data})
            fetch_start = time.perf_counter()
            page_items = list(queryset[offset : offset + limit])
            timings["db_fetch_ms"] += (time.perf_counter() - fetch_start) * 1000
            serializer_start = time.perf_counter()
            serializer = self.get_serializer(page_items, many=True)
            data = serializer.data
            timings["serializer_ms"] += (time.perf_counter() - serializer_start) * 1000
            return Response(data)

        def page_summary_response():
            queryset_start = time.perf_counter()
            queryset = self.filter_queryset(self.get_queryset())
            timings["queryset_ms"] += (time.perf_counter() - queryset_start) * 1000
            page_number = _bounded_int_query_param(request, "page", default=1, minimum=1, maximum=100000)
            page_size = _bounded_int_query_param(
                request,
                "page_size",
                default=PRODUCT_LIST_PAGE_SIZE,
                minimum=1,
                maximum=100,
            )
            count_start = time.perf_counter()
            total = queryset.count()
            timings["count_ms"] += (time.perf_counter() - count_start) * 1000
            offset = (page_number - 1) * page_size
            fetch_start = time.perf_counter()
            page_items = list(queryset[offset : offset + page_size])
            timings["db_fetch_ms"] += (time.perf_counter() - fetch_start) * 1000
            serializer_start = time.perf_counter()
            serializer = self.get_serializer(page_items, many=True)
            data = serializer.data
            timings["serializer_ms"] += (time.perf_counter() - serializer_start) * 1000
            return Response(
                {
                    "count": total,
                    "page": page_number,
                    "page_size": page_size,
                    "total_pages": max(1, (total + page_size - 1) // page_size),
                    "results": data,
                }
            )

        def log_query_count(response):
            if should_profile:
                slowest = _slowest_sql_query(query_start)
                logger.info(
                    (
                        "ProductViewSet.list path=%s status=%s total_ms=%.1f queryset_ms=%.1f "
                        "count_ms=%.1f db_fetch_ms=%.1f serializer_ms=%.1f filters_generation_ms=%.1f "
                        "queries=%s slowest_sql_ms=%s slowest_sql=%s"
                    ),
                    request.get_full_path(),
                    getattr(response, "status_code", None),
                    (time.perf_counter() - request_start) * 1000,
                    timings["queryset_ms"],
                    timings["count_ms"],
                    timings["db_fetch_ms"],
                    timings["serializer_ms"],
                    timings["filters_generation_ms"],
                    len(connection.queries) - query_start,
                    f"{slowest['time_ms']:.1f}" if slowest else "0.0",
                    slowest["sql"] if slowest else "",
                )
                connection.force_debug_cursor = previous_force_debug_cursor
            return response

        is_admin_request = bool(request.user and request.user.is_authenticated and request.user.is_staff)
        can_cache = (
            request.method == "GET"
            and not is_admin_request
            and PRODUCT_LIST_CACHE_TTL > 0
            and _has_usable_cache_backend()
        )
        if not can_cache:
            if is_page_summary:
                return log_query_count(page_summary_response())
            if is_limited_summary:
                return log_query_count(limited_summary_response())
            return log_query_count(super().list(request, *args, **kwargs))

        query_string = urlencode(sorted(request.query_params.lists()), doseq=True)
        cache_key = f"product-list:v8:{query_string}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return log_query_count(Response(cached_data))

        if is_page_summary:
            response = page_summary_response()
        elif is_limited_summary:
            response = limited_summary_response()
        else:
            response = super().list(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            cache.set(cache_key, response.data, PRODUCT_LIST_CACHE_TTL)
        return log_query_count(response)

    def retrieve(self, request, *args, **kwargs):
        is_admin_request = bool(request.user and request.user.is_authenticated and request.user.is_staff)
        can_cache = (
            request.method == "GET"
            and not is_admin_request
            and PRODUCT_DETAIL_CACHE_TTL > 0
            and _has_usable_cache_backend()
        )
        if not can_cache:
            return super().retrieve(request, *args, **kwargs)

        response_kind = "quick" if self._is_quick_detail_request() else "core" if self._is_core_detail_request() else "full"
        cache_key = f"product-detail:v5:{response_kind}:{kwargs.get(self.lookup_field or 'pk')}"
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return Response(cached_data)

        response = super().retrieve(request, *args, **kwargs)
        if response.status_code == status.HTTP_200_OK:
            cache.set(cache_key, response.data, PRODUCT_DETAIL_CACHE_TTL)
        return response

    @action(detail=False, methods=["post"], permission_classes=[IsAdminUser], url_path="delete-fabric")
    def delete_fabric(self, request):
        fabric_name = str(request.data.get("name") or "").strip()
        raw_product_ids = request.data.get("product_ids") or []

        if not fabric_name:
            raise ValidationError({"name": "Fabric name is required."})
        if not isinstance(raw_product_ids, list):
            raise ValidationError({"product_ids": "Product IDs must be a list."})

        product_ids = []
        for raw_id in raw_product_ids:
            try:
                product_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if product_id > 0 and product_id not in product_ids:
                product_ids.append(product_id)

        if not product_ids:
            raise ValidationError({"product_ids": "Select at least one product."})

        queryset = ProductFabric.objects.filter(product_id__in=product_ids, name__iexact=fabric_name)
        affected_product_ids = list(queryset.values_list("product_id", flat=True).distinct())
        deleted_count, _ = queryset.delete()

        self._invalidate_cache()
        return Response(
            {
                "deleted_count": deleted_count,
                "product_count": len(affected_product_ids),
                "product_ids": affected_product_ids,
            }
        )

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        images = data.pop("images", [])
        videos = data.pop("videos", [])
        colors = data.pop("colors", [])
        sizes = data.pop("sizes", [])
        styles = data.pop("styles", [])
        fabrics = data.pop("fabrics", [])
        mattresses = data.pop("mattresses", [])
        filter_values = data.pop("filter_values", [])

        images, videos, colors, sizes, styles, fabrics, mattresses = self._validate_related_data(
            images, videos, colors, sizes, styles, fabrics, mattresses
        )

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        dimension_template_obj = serializer.validated_data.get("_dimension_template_obj")
        product = serializer.save()

        self._handle_related_data(product, images, videos, colors, sizes, styles, fabrics, mattresses)
        self._handle_filter_values(product, filter_values)
        self._handle_dimension_template(product, dimension_template_obj)

        self._invalidate_cache()
        if _wants_empty_success_response(request):
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(
            ProductSerializer(product, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def update(self, request, *args, **kwargs):
        data = request.data.copy()
        simple_visibility_update = set(data.keys()).issubset({"is_hidden"})
        has_images = "images" in data
        has_videos = "videos" in data
        has_colors = "colors" in data
        has_sizes = "sizes" in data
        has_styles = "styles" in data
        has_fabrics = "fabrics" in data
        has_mattresses = "mattresses" in data
        has_filter_values = "filter_values" in data

        if simple_visibility_update:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            product = serializer.save()
            self._invalidate_cache(product)
            if _wants_empty_success_response(request):
                return Response(status=status.HTTP_204_NO_CONTENT)
            refreshed = self._base_queryset().prefetch_related(*self._detail_prefetches).get(pk=instance.pk)
            return Response(ProductSerializer(refreshed, context=self.get_serializer_context()).data)

        images = data.pop("images", None) if has_images else None
        videos = data.pop("videos", None) if has_videos else None
        colors = data.pop("colors", None) if has_colors else None
        sizes = data.pop("sizes", None) if has_sizes else None
        styles = data.pop("styles", None) if has_styles else None
        fabrics = data.pop("fabrics", None) if has_fabrics else None
        mattresses = data.pop("mattresses", None) if has_mattresses else None
        filter_values = data.pop("filter_values", None) if has_filter_values else None

        images, videos, colors, sizes, styles, fabrics, mattresses = self._validate_related_data(
            images if has_images else [],
            videos if has_videos else [],
            colors if has_colors else [],
            sizes if has_sizes else [],
            styles if has_styles else [],
            fabrics if has_fabrics else [],
            mattresses if has_mattresses else [],
        )

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        dimension_template_obj = serializer.validated_data.get("_dimension_template_obj")
        product = serializer.save()

        if has_images:
            product.images.all().delete()
        if has_videos:
            product.videos.all().delete()
        if has_colors:
            product.colors.all().delete()
        if has_sizes:
            product.sizes.all().delete()
        if has_styles:
            product.styles.all().delete()
        if has_fabrics:
            product.fabrics.all().delete()
        if has_mattresses:
            product.mattresses.all().delete()

        self._handle_related_data(
            product,
            images if has_images else [],
            videos if has_videos else [],
            colors if has_colors else [],
            sizes if has_sizes else [],
            styles if has_styles else [],
            fabrics if has_fabrics else [],
            mattresses if has_mattresses else [],
        )
        if has_filter_values:
            self._handle_filter_values(product, filter_values)

        self._handle_dimension_template(product, dimension_template_obj)

        self._invalidate_cache(product)
        if _wants_empty_success_response(request):
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(ProductSerializer(product, context=self.get_serializer_context()).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="duplicate")
    def duplicate(self, request, pk=None):
        source = self.get_queryset().get(pk=pk)

        with transaction.atomic():
            duplicated_product = self._duplicate_product(source)

        self._invalidate_cache(duplicated_product)
        refreshed = self._base_queryset().prefetch_related(*self._detail_prefetches).get(pk=duplicated_product.pk)
        return Response(
            ProductSerializer(refreshed, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser], url_path="import-copy")
    def import_copy(self, request, pk=None):
        source = self.get_queryset().get(pk=pk)

        try:
            category_id = int(request.data.get("category"))
        except (TypeError, ValueError):
            raise ValidationError({"category": "A valid category ID is required."})

        raw_subcategory_id = request.data.get("subcategory")
        try:
            subcategory_id = int(raw_subcategory_id) if raw_subcategory_id not in (None, "", "null") else None
        except (TypeError, ValueError):
            raise ValidationError({"subcategory": "Subcategory must be a valid ID or null."})

        try:
            target_category = Category.objects.get(pk=category_id)
        except Category.DoesNotExist:
            raise ValidationError({"category": "Category not found."})

        target_subcategory = None
        if subcategory_id is not None:
            try:
                target_subcategory = SubCategory.objects.select_related("category").prefetch_related(
                    "additional_categories"
                ).get(pk=subcategory_id)
            except SubCategory.DoesNotExist:
                raise ValidationError({"subcategory": "Subcategory not found."})

            if not target_subcategory.is_linked_to_category(target_category):
                raise ValidationError(
                    {"subcategory": f"{target_subcategory.name} is not linked to {target_category.name}."}
                )

        # Always anchor placement copies to the original product. This prevents
        # copy-of-copy chains and lets repeated assignments reuse one target row.
        linked_source = source.imported_from_product
        source_name = " ".join(str(source.name or "").casefold().split())
        linked_source_name = " ".join(str(getattr(linked_source, "name", "") or "").casefold().split())
        canonical_source = (
            linked_source
            if linked_source and source_name and source_name == linked_source_name
            else source
        )

        if canonical_source.category_id == category_id and canonical_source.subcategory_id == subcategory_id:
            refreshed = self._base_queryset().prefetch_related(*self._detail_prefetches).get(pk=canonical_source.pk)
            return Response(
                ProductSerializer(refreshed, context=self.get_serializer_context()).data,
                status=status.HTTP_200_OK,
            )

        existing_import = Product.objects.filter(
            imported_from_product_id=canonical_source.id,
            category_id=category_id,
            subcategory_id=subcategory_id,
        ).first()
        if existing_import:
            refreshed = self._base_queryset().prefetch_related(*self._detail_prefetches).get(pk=existing_import.pk)
            return Response(
                ProductSerializer(refreshed, context=self.get_serializer_context()).data,
                status=status.HTTP_200_OK,
            )

        with transaction.atomic():
            imported_product = self._import_product_copy(canonical_source, target_category, target_subcategory)

        self._invalidate_cache(imported_product)
        refreshed = self._base_queryset().prefetch_related(*self._detail_prefetches).get(pk=imported_product.pk)
        return Response(
            ProductSerializer(refreshed, context=self.get_serializer_context()).data,
            status=status.HTTP_201_CREATED,
        )

    def _build_unique_slug(self, raw_value):
        max_length = Product._meta.get_field("slug").max_length or 255
        base = slugify(raw_value) or "product"
        base = base[:max_length]
        slug = base
        counter = 2
        while Product.objects.filter(slug=slug).exists():
            suffix = f"-{counter}"
            slug = f"{base[: max_length - len(suffix)]}{suffix}"
            counter += 1
        return slug

    def _build_duplicate_slug(self, product):
        return self._build_unique_slug(f"{product.slug or product.name}-copy")

    def _build_duplicate_name(self, product):
        base = f"{product.name} (Copy)"
        candidate = base
        counter = 2
        while Product.objects.filter(name=candidate).exists():
            candidate = f"{base} {counter}"
            counter += 1
        return candidate

    def _build_import_slug(self, product, category, subcategory=None):
        target_scope = getattr(subcategory, "slug", "") or getattr(category, "slug", "") or "import"
        base_value = f"{product.slug or product.name}-{target_scope}"
        return self._build_unique_slug(base_value)

    def _next_sort_order_for_scope(self, category_id, subcategory_id):
        scoped_products = Product.objects.filter(category_id=category_id)
        if subcategory_id is not None:
            scoped_products = scoped_products.filter(subcategory_id=subcategory_id)

        last_sort_order = (
            scoped_products.exclude(sort_order__lte=0)
            .order_by("-sort_order")
            .values_list("sort_order", flat=True)
            .first()
        )
        if last_sort_order:
            return int(last_sort_order) + 1
        existing_count = scoped_products.count()
        return existing_count + 1 if existing_count > 0 else 1

    def _create_cloned_product(
        self,
        source,
        *,
        name,
        slug,
        category,
        subcategory,
        is_hidden,
        sort_order,
        imported_from_product=None,
    ):
        cloned_product = Product.objects.create(
            name=name,
            slug=slug,
            meta_title=source.meta_title,
            meta_description=source.meta_description,
            google_feed_brand=source.google_feed_brand,
            google_feed_sku=source.google_feed_sku,
            google_feed_mpn=source.google_feed_mpn,
            google_feed_gtin=source.google_feed_gtin,
            google_feed_special_feature=source.google_feed_special_feature,
            google_feed_color=source.google_feed_color,
            google_feed_material=source.google_feed_material,
            google_feed_fabric_type=source.google_feed_fabric_type,
            google_feed_frame_material=source.google_feed_frame_material,
            google_feed_headboard_material=source.google_feed_headboard_material,
            google_feed_number_of_drawers=source.google_feed_number_of_drawers,
            google_feed_depth=source.google_feed_depth,
            google_feed_length=source.google_feed_length,
            google_feed_width=source.google_feed_width,
            google_feed_height=source.google_feed_height,
            google_feed_seat_height=source.google_feed_seat_height,
            google_feed_variants=source.google_feed_variants,
            category=category,
            subcategory=subcategory,
            imported_from_product=imported_from_product,
            price=source.price,
            original_price=source.original_price,
            discount_percentage=source.discount_percentage,
            description=source.description,
            short_description=source.short_description,
            features=source.features,
            sofa_feature_highlights=source.sofa_feature_highlights,
            dimensions=source.dimensions,
            faqs=source.faqs,
            delivery_info=source.delivery_info,
            returns_guarantee=source.returns_guarantee,
            delivery_title=source.delivery_title,
            returns_title=source.returns_title,
            custom_info_sections=source.custom_info_sections,
            delivery_charges=source.delivery_charges,
            assembly_service_enabled=source.assembly_service_enabled,
            assembly_service_price=source.assembly_service_price,
            stock_status=source.resolved_stock_status(),
            in_stock=source.in_stock,
            is_hidden=is_hidden,
            is_bestseller=source.is_bestseller,
            is_new=source.is_new,
            show_size_icons=source.show_size_icons,
            rating=0,
            review_count=0,
            dimension_paragraph=source.dimension_paragraph,
            dimension_note=source.dimension_note,
            dimension_images=source.dimension_images,
            show_dimensions_table=source.show_dimensions_table,
            sort_order=sort_order,
        )
        cloned_product.suggested_products.set(source.suggested_products.exclude(pk=source.pk))
        self._clone_related_data(source, cloned_product)
        return cloned_product

    def _clone_related_data(self, source, cloned_product):
        source_images = list(source.images.all())
        source_videos = list(source.videos.all())
        source_colors = list(source.colors.all())
        source_sizes = list(source.sizes.all())
        source_styles = list(source.styles.all())
        source_fabrics = list(source.fabrics.all())
        source_mattresses = list(source.mattresses.all())
        source_filter_values = list(source.filter_values.all())

        for image in source_images:
            ProductImage.objects.create(
                product=cloned_product,
                url=image.url,
                color_name=image.color_name,
                style_name=image.style_name,
                alt_text=image.alt_text,
                flip_horizontal=image.flip_horizontal,
                sort_order=image.sort_order,
            )

        for video in source_videos:
            ProductVideo.objects.create(product=cloned_product, url=video.url)

        for color in source_colors:
            ProductColor.objects.create(
                product=cloned_product,
                name=color.name,
                hex_code=color.hex_code,
                image_url=color.image_url,
                is_available=color.is_available,
                stock_status=color.stock_status,
            )

        size_map = {}
        for size in source_sizes:
            cloned_size = ProductSize.objects.create(
                product=cloned_product,
                name=size.name,
                description=size.description,
                price_delta=size.price_delta,
                stock_status=size.stock_status,
            )
            size_map[size.id] = cloned_size

        for style in source_styles:
            ProductStyle.objects.create(
                product=cloned_product,
                size=size_map.get(style.size_id),
                is_shared=style.is_shared,
                name=style.name,
                icon_url=style.icon_url,
                options=style.options,
            )

        for fabric in source_fabrics:
            ProductFabric.objects.create(
                product=cloned_product,
                name=fabric.name,
                image_url=fabric.image_url,
                is_shared=fabric.is_shared,
                colors=fabric.colors,
            )

        for mattress in source_mattresses:
            ProductMattress.objects.create(
                product=cloned_product,
                source_product=mattress.source_product,
                name=mattress.name,
                description=mattress.description,
                image_url=mattress.image_url,
                price=mattress.price,
                enable_bunk_positions=mattress.enable_bunk_positions,
                price_top=mattress.price_top,
                price_bottom=mattress.price_bottom,
                price_both=mattress.price_both,
                is_hidden=mattress.is_hidden,
            )

        for filter_value in source_filter_values:
            ProductFilterValue.objects.create(
                product=cloned_product,
                filter_option=filter_value.filter_option,
            )

        if hasattr(source, "dimension_template_link"):
            ProductDimensionTemplate.objects.create(
                product=cloned_product,
                template=source.dimension_template_link.template,
                allow_overrides=source.dimension_template_link.allow_overrides,
            )

    def _duplicate_product(self, source):
        return self._create_cloned_product(
            source,
            name=self._build_duplicate_name(source),
            slug=self._build_duplicate_slug(source),
            category=source.category,
            subcategory=source.subcategory,
            is_hidden=True,
            sort_order=0,
        )

    def _import_product_copy(self, source, category, subcategory=None):
        return self._create_cloned_product(
            source,
            name=source.name,
            slug=self._build_import_slug(source, category, subcategory),
            category=category,
            subcategory=subcategory,
            is_hidden=source.is_hidden,
            sort_order=self._next_sort_order_for_scope(category.id, getattr(subcategory, "id", None)),
            imported_from_product=source,
        )

    def _handle_related_data(self, product, images, videos, colors, sizes, styles, fabrics, mattresses):
        for img in images:
            ProductImage.objects.create(
                product=product,
                url=img.get("url"),
                color_name=img.get("color_name", ""),
                style_name=img.get("style_name", ""),
                alt_text=img.get("alt_text", ""),
                flip_horizontal=bool(img.get("flip_horizontal", False)),
                sort_order=img.get("sort_order", 0),
            )
        for vid in videos:
            ProductVideo.objects.create(product=product, url=vid.get("url"))
        for col in colors:
            ProductColor.objects.create(
                product=product,
                name=col.get("name", ""),
                hex_code=col.get("hex_code", "#000000"),
                image_url=col.get("image_url", ""),
                is_available=col.get("is_available", True),
                stock_status=col.get("stock_status", ProductColor.STOCK_STATUS_AVAILABLE),
            )
        size_objs = []
        for size in sizes:
            size_obj = ProductSize.objects.create(
                product=product,
                name=size.get("name", ""),
                description=size.get("description", ""),
                price_delta=size.get("price_delta", 0),
                stock_status=size.get("stock_status", ProductSize.STOCK_STATUS_AVAILABLE),
            )
            size_objs.append(size_obj)
        size_lookup = {s.name.strip().lower(): s for s in size_objs}
        size_lookup.update({str(s.id): s for s in size_objs})
        for style in styles:
            size_ref = style.get("size")
            size_obj = None
            if size_ref:
                key = str(size_ref).strip().lower()
                size_obj = size_lookup.get(key)
            ProductStyle.objects.create(
                product=product,
                size=size_obj,
                is_shared=bool(style.get("is_shared", False)),
                name=style.get("name"),
                icon_url=style.get("icon_url", ""),
                options=style.get("options", []),
            )
        for fabric in fabrics:
            ProductFabric.objects.create(
                product=product,
                name=fabric.get("name", ""),
                image_url=fabric.get("image_url", ""),
                is_shared=bool(fabric.get("is_shared", False)),
                colors=fabric.get("colors", []),
            )
        for mattress in mattresses:
            source_product = None
            source_id = mattress.get("source_product")
            if source_id:
                try:
                    source_product = Product.objects.get(id=source_id)
                except Product.DoesNotExist:
                    source_product = None
            ProductMattress.objects.create(
                product=product,
                source_product=source_product,
                name=mattress.get("name", ""),
                description=mattress.get("description", ""),
                image_url=mattress.get("image_url", ""),
                price=mattress.get("price", None),
                enable_bunk_positions=bool(mattress.get("enable_bunk_positions", False)),
                price_top=mattress.get("price_top", None),
                price_bottom=mattress.get("price_bottom", None),
                price_both=mattress.get("price_both", None),
                is_hidden=bool(mattress.get("is_hidden", False)),
            )

    def _validate_related_data(self, images, videos, colors, sizes, styles, fabrics, mattresses):
        def _coerce_bool(value, default=True):
            if value is None:
                return default
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes", "on"}:
                    return True
                if normalized in {"false", "0", "no", "off"}:
                    return False
            return bool(value)

        def _normalize_color_stock_status(item):
            raw_status = str((item or {}).get("stock_status") or "").strip().lower()
            if raw_status in {
                ProductColor.STOCK_STATUS_AVAILABLE,
                ProductColor.STOCK_STATUS_OUT_OF_STOCK,
                ProductColor.STOCK_STATUS_STOCK_CHECK_NEEDED,
            }:
                return raw_status
            is_available = _coerce_bool((item or {}).get("is_available", True), default=True)
            return ProductColor.STOCK_STATUS_AVAILABLE if is_available else ProductColor.STOCK_STATUS_OUT_OF_STOCK

        def _normalize_size_stock_status(item):
            raw_status = str((item or {}).get("stock_status") or "").strip().lower()
            if raw_status in {
                ProductSize.STOCK_STATUS_AVAILABLE,
                ProductSize.STOCK_STATUS_OUT_OF_STOCK,
                ProductSize.STOCK_STATUS_STOCK_CHECK_NEEDED,
            }:
                return raw_status
            return ProductSize.STOCK_STATUS_AVAILABLE

        image_url_max = ProductImage._meta.get_field("url").max_length
        image_color_max = ProductImage._meta.get_field("color_name").max_length
        image_style_max = ProductImage._meta.get_field("style_name").max_length
        image_alt_max = ProductImage._meta.get_field("alt_text").max_length
        video_url_max = ProductVideo._meta.get_field("url").max_length
        color_name_max = ProductColor._meta.get_field("name").max_length
        size_name_max = ProductSize._meta.get_field("name").max_length
        size_desc_max = ProductSize._meta.get_field("description").max_length
        style_name_max = ProductStyle._meta.get_field("name").max_length
        fabric_name_max = ProductFabric._meta.get_field("name").max_length
        fabric_url_max = ProductFabric._meta.get_field("image_url").max_length
        mattress_name_max = ProductMattress._meta.get_field("name").max_length
        mattress_image_max = ProductMattress._meta.get_field("image_url").max_length
        mattress_price_max_digits = ProductMattress._meta.get_field("price").max_digits

        cleaned_images = []
        for img in images:
            url = str((img or {}).get("url", "")).strip()
            color_name = str((img or {}).get("color_name", "")).strip()
            style_name = str((img or {}).get("style_name", "")).strip()
            alt_text = str((img or {}).get("alt_text", "")).strip()
            flip_horizontal = bool((img or {}).get("flip_horizontal", False))
            raw_sort_order = (img or {}).get("sort_order", 0)
            if not url:
                continue
            if len(url) > image_url_max:
                raise ValidationError({"images": [f"Image URL too long (max {image_url_max} chars)."]})
            if color_name and len(color_name) > image_color_max:
                raise ValidationError({"images": [f"Image color name too long (max {image_color_max} chars)."]})
            if style_name and len(style_name) > image_style_max:
                raise ValidationError({"images": [f"Image style name too long (max {image_style_max} chars)."]})
            if alt_text and len(alt_text) > image_alt_max:
                raise ValidationError({"images": [f"Image alt text too long (max {image_alt_max} chars)."]})
            try:
                sort_order = max(int(raw_sort_order or 0), 0)
            except (TypeError, ValueError):
                sort_order = 0
            cleaned_images.append(
                {
                    "url": url,
                    "color_name": color_name,
                    "style_name": style_name,
                    "alt_text": alt_text,
                    "flip_horizontal": flip_horizontal,
                    "sort_order": sort_order,
                }
            )

        cleaned_videos = []
        for vid in videos:
            url = str((vid or {}).get("url", "")).strip()
            if not url:
                continue
            if len(url) > video_url_max:
                raise ValidationError({"videos": [f"Video URL too long (max {video_url_max} chars)."]})
            cleaned_videos.append({"url": url})

        cleaned_colors = []
        for col in colors:
            name = str((col or {}).get("name", "")).strip()
            if not name:
                continue
            if len(name) > color_name_max:
                raise ValidationError({"colors": [f"Color name too long (max {color_name_max} chars)."]})
            hex_code = str((col or {}).get("hex_code", "#000000")).strip() or "#000000"
            image_url = str((col or {}).get("image_url", "")).strip()
            stock_status = _normalize_color_stock_status(col)
            cleaned_colors.append(
                {
                    "name": name,
                    "hex_code": hex_code,
                    "image_url": image_url,
                    "is_available": stock_status != ProductColor.STOCK_STATUS_OUT_OF_STOCK,
                    "stock_status": stock_status,
                }
            )

        cleaned_sizes = []
        for size in sizes:
            if isinstance(size, dict):
                value = str(size.get("name", "")).strip()
                description = str(size.get("description", "")).strip()
                raw_delta = size.get("price_delta", 0)
            else:
                value = str(size).strip()
                description = ""
                raw_delta = 0

            if not value:
                continue
            if len(value) > size_name_max:
                raise ValidationError({"sizes": [f"Size value too long (max {size_name_max} chars)."]})
            if len(description) > size_desc_max:
                raise ValidationError({"sizes": [f"Size description too long (max {size_desc_max} chars)."]})
            try:
                delta = Decimal(raw_delta)
            except (InvalidOperation, TypeError):
                raise ValidationError({"sizes": [f"Invalid price_delta for size '{value}'. Provide a number."]})
            cleaned_sizes.append(
                {
                    "name": value,
                    "description": description,
                    "price_delta": delta,
                    "stock_status": _normalize_size_stock_status(size),
                }
            )

        cleaned_styles = []
        max_style_option_icon_length = 200000  # allow inline SVG but block payload explosions
        # Allow letters, numbers, dot/underscore/dash, spaces, and common punctuation used in sizes (quotes, apostrophes, parentheses)
        # Relax validation: allow any characters (length limits still enforced)
        for style in styles:
            name = str((style or {}).get("name", "")).strip()
            if not name:
                continue
            if len(name) > style_name_max:
                raise ValidationError({"styles": [f"Style name too long (max {style_name_max} chars)."]})
            # No character whitelist beyond length
            style_icon = str((style or {}).get("icon_url", "")).strip()
            if len(style_icon) > max_style_option_icon_length:
                raise ValidationError({"styles": [f"Style icon is too large (max {max_style_option_icon_length} chars)."]})

            options = (style or {}).get("options", [])
            normalized_options = []
            if isinstance(options, list):
                for option in options:
                    if isinstance(option, str):
                        label = option.strip()
                        if label:
                            normalized_options.append({"label": label, "description": "", "icon_url": "", "price_delta": 0})
                        continue
                    if not isinstance(option, dict):
                        continue
                    label = str(option.get("label", option.get("name", ""))).strip()
                    if not label:
                        continue
                    description = str(option.get("description", "")).strip()
                    icon_url = str(option.get("icon_url", "")).strip()
                    size_val = str(option.get("size", "") or "").strip()
                    raw_sizes = option.get("sizes", [])
                    sizes = []
                    if isinstance(raw_sizes, list):
                        for s in raw_sizes:
                            sval = str(s or "").strip()
                            if sval:
                                sizes.append(sval)
                    if size_val and size_val not in sizes:
                        sizes.append(size_val)
                    use_size_pricing = bool(option.get("use_size_pricing", False))
                    raw_overrides = option.get("size_price_overrides", {})
                    size_price_overrides = {}
                    if isinstance(raw_overrides, dict):
                        for size_key, override_value in raw_overrides.items():
                            normalized_size = str(size_key or "").strip()
                            if not normalized_size:
                                continue
                            try:
                                size_price_overrides[normalized_size] = float(override_value)
                            except Exception:
                                raise ValidationError(
                                    {"styles": [f"Invalid size-specific price for option '{label}' and size '{normalized_size}'."]}
                                )
                    price_delta = option.get("price_delta", option.get("delta", 0))
                    try:
                        price_delta = float(price_delta or 0)
                    except Exception:
                        price_delta = 0
                    if len(icon_url) > max_style_option_icon_length:
                        raise ValidationError({"styles": [f"Style option icon is too large (max {max_style_option_icon_length} chars)."]})
                    normalized_options.append(
                        {
                            "label": label,
                            "description": description,
                            "icon_url": icon_url,
                            "price_delta": price_delta,
                            "sizes": sizes,
                            "use_size_pricing": use_size_pricing or bool(size_price_overrides),
                            "size_price_overrides": size_price_overrides,
                        }
                    )

            cleaned_styles.append({
                "name": name,
                "icon_url": style_icon,
                "options": normalized_options,
                "is_shared": bool((style or {}).get("is_shared", False)),
                "size": (style or {}).get("size"),
            })

        cleaned_fabrics = []
        for fabric in fabrics:
            name = str((fabric or {}).get("name", "")).strip()
            image_url = str((fabric or {}).get("image_url", "")).strip()
            is_shared = bool((fabric or {}).get("is_shared", False))
            colors_list = []
            for col in (fabric or {}).get("colors", []) or []:
                if not isinstance(col, dict):
                    continue
                cname = str(col.get("name", "")).strip()
                if not cname:
                    continue
                stock_status = _normalize_color_stock_status(col)
                colors_list.append({
                    "name": cname,
                    "hex_code": str(col.get("hex_code", "#000000")).strip() or "#000000",
                    "image_url": str(col.get("image_url", "")).strip(),
                    "is_available": stock_status != ProductColor.STOCK_STATUS_OUT_OF_STOCK,
                    "stock_status": stock_status,
                })
            if not name and not image_url:
                continue
            if len(name) > fabric_name_max:
                raise ValidationError({"fabrics": [f"Fabric name too long (max {fabric_name_max} chars)."]})
            if len(image_url) > fabric_url_max:
                raise ValidationError({"fabrics": [f"Fabric image URL too long (max {fabric_url_max} chars)."]})
            cleaned_fabrics.append({"name": name, "image_url": image_url, "is_shared": is_shared, "colors": colors_list})

        cleaned_mattresses = []
        for mat in mattresses:
            name = str((mat or {}).get("name", "")).strip()
            description = str((mat or {}).get("description", "")).strip()
            image_url = str((mat or {}).get("image_url", "")).strip()
            source_product = mat.get("source_product")
            enable_bunk_positions = bool((mat or {}).get("enable_bunk_positions", False))
            is_hidden = bool((mat or {}).get("is_hidden", False))
            def _clean_price(field):
                raw = mat.get(field, None)
                if raw in (None, "", "null"):
                    return None
                try:
                    return Decimal(raw)
                except (InvalidOperation, TypeError):
                    raise ValidationError({"mattresses": [f"Invalid {field.replace('_',' ')} for mattress '{name or 'untitled'}'."]})

            price = _clean_price("price")
            price_top = _clean_price("price_top")
            price_bottom = _clean_price("price_bottom")
            price_both = _clean_price("price_both")
            if name and len(name) > mattress_name_max:
                raise ValidationError({"mattresses": [f"Mattress name too long (max {mattress_name_max} chars)."]})
            if image_url and len(image_url) > mattress_image_max:
                raise ValidationError({"mattresses": [f"Mattress image URL too long (max {mattress_image_max} chars)."]})
            if not any([name, description, image_url, price, source_product]):
                continue
            cleaned_mattresses.append(
                {
                    "name": name,
                    "description": description,
                    "image_url": image_url,
                    "price": price,
                    "source_product": source_product,
                    "enable_bunk_positions": enable_bunk_positions,
                    "price_top": price_top,
                    "price_bottom": price_bottom,
                    "price_both": price_both,
                    "is_hidden": is_hidden,
                }
            )

        return cleaned_images, cleaned_videos, cleaned_colors, cleaned_sizes, cleaned_styles, cleaned_fabrics, cleaned_mattresses

    def _handle_filter_values(self, product, filter_values):
        product.filter_values.all().delete()
        cleaned = []
        for fv in filter_values or []:
            opt_id = fv.get("filter_option") if isinstance(fv, dict) else fv
            if not opt_id:
                continue
            try:
                option = FilterOption.objects.get(id=opt_id)
            except FilterOption.DoesNotExist:
                continue
            cleaned.append(option)
        for option in cleaned:
            ProductFilterValue.objects.create(product=product, filter_option=option)

    def _handle_dimension_template(self, product, dimension_template_obj):
        # Remove existing link if cleared
        if dimension_template_obj is None:
            ProductDimensionTemplate.objects.filter(product=product).delete()
            return
        link, _ = ProductDimensionTemplate.objects.get_or_create(product=product)
        link.template = dimension_template_obj
        link.save()


class MattressOptionViewSet(viewsets.ModelViewSet):
    """
    Admin-defined global mattress options with per-size pricing.
    Public GET is allowed; write requires admin.
    """

    queryset = MattressOption.objects.all().prefetch_related("prices", "categories", "subcategories", "products")
    serializer_class = MattressOptionSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def _invalidate_cache(self):
        cache.clear()

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .annotate(
                sort_priority=Case(
                    When(sort_order__gt=0, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("sort_priority", "sort_order", "name")
        )

    @action(detail=False, methods=["get"], permission_classes=[IsAdminUser], url_path="product-mattresses")
    def product_mattresses(self, request):
        queryset = (
            ProductMattress.objects.select_related("product", "product__category", "product__subcategory", "source_product")
            .order_by("product__name", "id")
        )
        serializer = ProductMattressSerializer(queryset, many=True)
        return Response(serializer.data)

    def _clean_prices(self, prices_raw):
        cleaned = []
        for p in prices_raw or []:
            if not isinstance(p, dict):
                continue
            size_label = str(p.get("size_label", "")).strip()
            if not size_label:
                continue

            def _clean_decimal(val):
                if val in (None, "", "null"):
                    return None
                try:
                    return Decimal(val)
                except (InvalidOperation, TypeError):
                    raise ValidationError({"prices": [f"Invalid price value for size '{size_label}'."]})

            cleaned.append(
                {
                    "size_label": size_label,
                    "price": _clean_decimal(p.get("price")),
                    "original_price": _clean_decimal(p.get("original_price")),
                    "price_top": _clean_decimal(p.get("price_top")),
                    "price_bottom": _clean_decimal(p.get("price_bottom")),
                    "price_both": _clean_decimal(p.get("price_both")),
                }
            )
        return cleaned

    def _clean_categories(self, categories_raw):
        cleaned = []
        for cid in categories_raw or []:
            try:
                cat = Category.objects.get(id=cid)
                cleaned.append(cat)
            except Category.DoesNotExist:
                continue
        return cleaned

    def _clean_subcategories(self, subcategories_raw):
        cleaned = []
        for sid in subcategories_raw or []:
            try:
                sub = SubCategory.objects.get(id=sid)
                cleaned.append(sub)
            except SubCategory.DoesNotExist:
                continue
        return cleaned

    def _clean_products(self, products_raw):
        cleaned = []
        for pid in products_raw or []:
            try:
                product = Product.objects.get(id=pid)
                cleaned.append(product)
            except Product.DoesNotExist:
                continue
        return cleaned

    def _upsert_prices(self, option, prices):
        option.prices.all().delete()
        for p in prices:
            MattressOptionPrice.objects.create(option=option, **p)

    def _next_sort_order_for_button_label(self, button_label):
        scoped_options = MattressOption.objects.filter(
            kids_button_label__iexact=str(button_label or "").strip()
        )
        last_sort_order = (
            scoped_options.exclude(sort_order__lte=0)
            .order_by("-sort_order")
            .values_list("sort_order", flat=True)
            .first()
        )
        if last_sort_order:
            return int(last_sort_order) + 1
        existing_count = scoped_options.count()
        return existing_count + 1 if existing_count > 0 else 1

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        prices_raw = data.pop("prices", [])
        categories_raw = data.pop("categories", [])
        subcategories_raw = data.pop("subcategories", [])
        products_raw = data.pop("products", [])
        prices = self._clean_prices(prices_raw)
        categories = self._clean_categories(categories_raw)
        subcategories = self._clean_subcategories(subcategories_raw)
        products = self._clean_products(products_raw)
        try:
            requested_sort_order = int(data.get("sort_order") or 0)
        except (TypeError, ValueError):
            requested_sort_order = 0
        if requested_sort_order <= 0:
            data["sort_order"] = self._next_sort_order_for_button_label(data.get("kids_button_label", ""))
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        option = serializer.save()
        self._upsert_prices(option, prices)
        if categories is not None:
            option.categories.set(categories)
        if subcategories is not None:
            option.subcategories.set(subcategories)
        if products is not None:
            option.products.set(products)
        self._invalidate_cache()
        return Response(self.get_serializer(option).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        data = request.data.copy()
        prices_raw = data.pop("prices", None)
        categories_raw = data.pop("categories", None)
        subcategories_raw = data.pop("subcategories", None)
        products_raw = data.pop("products", None)
        prices = self._clean_prices(prices_raw) if prices_raw is not None else None
        categories = self._clean_categories(categories_raw) if categories_raw is not None else None
        subcategories = self._clean_subcategories(subcategories_raw) if subcategories_raw is not None else None
        products = self._clean_products(products_raw) if products_raw is not None else None
        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        option = serializer.save()
        if prices is not None:
            self._upsert_prices(option, prices)
        if categories is not None:
            option.categories.set(categories)
        if subcategories is not None:
            option.subcategories.set(subcategories)
        if products is not None:
            option.products.set(products)
        self._invalidate_cache()
        return Response(self.get_serializer(option).data)

    def destroy(self, request, *args, **kwargs):
        response = super().destroy(request, *args, **kwargs)
        self._invalidate_cache()
        return response


class ProductMattressAdminViewSet(viewsets.ModelViewSet):
    queryset = ProductMattress.objects.select_related(
        "product", "product__category", "product__subcategory", "source_product"
    ).order_by("product__name", "id")
    serializer_class = ProductMattressSerializer
    permission_classes = [IsAdminUser]
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def _invalidate_cache(self):
        cache.clear()

    def partial_update(self, request, *args, **kwargs):
        response = super().partial_update(request, *args, **kwargs)
        self._invalidate_cache()
        return response


class ProductAddonViewSet(viewsets.ModelViewSet):
    queryset = ProductAddon.objects.select_related("main_product", "addon_product").prefetch_related("addon_product__images")
    serializer_class = ProductAddonSerializer
    permission_classes = [IsAdminUser]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def perform_create(self, serializer):
        serializer.save()
        cache.clear()

    def perform_update(self, serializer):
        serializer.save()
        cache.clear()

    def perform_destroy(self, instance):
        instance.delete()
        cache.clear()

    def destroy(self, request, *args, **kwargs):
        response = super().destroy(request, *args, **kwargs)
        self._invalidate_cache()
        return response


class PromotionViewSet(viewsets.ModelViewSet):
    queryset = Promotion.objects.all().prefetch_related("categories", "subcategories").order_by(
        "sort_order", "start_date", "name"
    )
    serializer_class = PromotionSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self.request.user, "is_staff", False):
            return queryset
        return _get_live_promotions()

    @action(detail=False, methods=["get"], permission_classes=[AllowAny])
    def announcement(self, request):
        promotion = _get_live_promotions().exclude(announcement_text="").first()
        if promotion:
            return Response(
                {
                    "text": promotion.announcement_text,
                    "promotion": _serialize_public_promotion(promotion),
                    "is_default": False,
                }
            )

        return Response(
            {
                "text": (_get_announcement_settings().default_text or "").strip(),
                "promotion": None,
                "is_default": True,
            }
        )

    @action(detail=False, methods=["get", "put", "patch"], permission_classes=[IsAdminUser], url_path="default-announcement")
    def default_announcement(self, request):
        settings_obj = _get_announcement_settings()
        if request.method == "GET":
            return Response(AnnouncementSettingsSerializer(settings_obj).data)

        serializer = AnnouncementSettingsSerializer(
            settings_obj,
            data=request.data,
            partial=request.method == "PATCH",
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, methods=["post"], permission_classes=[AllowAny])
    def availability(self, request):
        items_payload = request.data.get("items", []) or []
        product_ids = [item.get("product_id") for item in items_payload if item.get("product_id")]
        products = Product.objects.filter(id__in=product_ids).select_related("category", "subcategory")
        product_lookup = {product.id: product for product in products}

        applicable_promotions = []
        for promotion in _get_live_promotions():
            if any(
                product_lookup.get(item.get("product_id"))
                and _promotion_applies_to_product(promotion, product_lookup[item.get("product_id")])
                for item in items_payload
            ):
                applicable_promotions.append(_serialize_public_promotion(promotion))

        return Response(
            {
                "has_applicable_promotion": bool(applicable_promotions),
                "promotions": applicable_promotions,
            }
        )

    @action(detail=False, methods=["post"], permission_classes=[AllowAny])
    def validate_code(self, request):
        items_payload = request.data.get("items", []) or []
        result = _build_promotion_result(code=request.data.get("code"), items_payload=items_payload)
        return Response(
            {
                "promotion_id": result["promotion"].id,
                "promotion_name": result["promotion"].name,
                "code": result["promotion"].code,
                "discount_percentage": float(result["discount_percentage"]),
                "discount_amount": float(result["discount_amount"]),
                "applicable_product_ids": list(result["applicable_product_ids"]),
                "line_results": result["line_results"],
            }
        )


class OrderViewSet(viewsets.ModelViewSet):
    queryset = Order.objects.all().prefetch_related("items__product").order_by("-created_at")
    serializer_class = OrderSerializer
    ORDER_REFERENCE_IMAGE_MAX_SIZE = int(
        getattr(settings, "ORDER_REFERENCE_IMAGE_MAX_UPLOAD_SIZE", 10 * 1024 * 1024)
    )

    def get_permissions(self):
        if self.action in ("create", "lookup", "upload_reference_image"):
            return [AllowAny()]
        if self.action == "mark_paid":
            if self.request.user.is_staff:
                return [IsAdminUser()]
            return [AllowAny()]
        if self.action == "mark_cancelled":
            return [IsAdminUser()]
        if self.action == "delivery_note_pdf":
            return [IsAdminUser()]
        if self.request.user.is_staff:
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def get_queryset(self):
        if self.request.user.is_staff:
            return super().get_queryset()
        return super().get_queryset().filter(user=self.request.user)

    def _is_admin_request(self, request) -> bool:
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)

    def _get_order_by_id(self, order_id):
        try:
            return Order.objects.prefetch_related("items__product").get(pk=order_id)
        except Order.DoesNotExist as exc:
            raise NotFound("Order not found") from exc

    def _get_request_email(self, request) -> str:
        return str(request.data.get("email") or request.query_params.get("email") or "").strip()

    def _can_access_order(self, request, order, *, allow_email_match: bool = False) -> bool:
        if self._is_admin_request(request):
            return True
        if request.user and request.user.is_authenticated and order.user_id and request.user.id == order.user_id:
            return True
        if allow_email_match:
            provided_email = self._get_request_email(request)
            order_email = str(order.email or "").strip()
            if provided_email and order_email and provided_email.casefold() == order_email.casefold():
                return True
        return False

    def _require_order_access(self, request, order, *, allow_email_match: bool = False, error_message: str):
        if self._can_access_order(request, order, allow_email_match=allow_email_match):
            return
        raise ValidationError({"email": error_message})

    def _sync_cancelled_timestamp(self, order) -> None:
        update_fields = []
        if order.status == "cancelled" and not order.cancelled_at:
            order.cancelled_at = timezone.now()
            update_fields.append("cancelled_at")
        elif order.status != "cancelled" and order.cancelled_at is not None:
            order.cancelled_at = None
            update_fields.append("cancelled_at")

        if update_fields:
            order.save(update_fields=update_fields)

    def _normalized_payment_method(self, value: str | None) -> str:
        return str(value or "").strip().lower()

    def _merge_payment_metadata(self, order, incoming_metadata=None):
        merged = dict(order.payment_metadata or {})
        if isinstance(incoming_metadata, dict):
            merged.update(incoming_metadata)
        return merged

    def _apply_payment_details(
        self,
        order,
        *,
        payment_method: str | None = None,
        payment_id: str | None = None,
        payment_metadata: dict | None = None,
        status_value: str | None = None,
    ) -> None:
        update_fields = []

        if payment_method is not None:
            normalized_method = str(payment_method or "").strip()
            if order.payment_method != normalized_method:
                order.payment_method = normalized_method
                update_fields.append("payment_method")

        if payment_id is not None:
            normalized_payment_id = str(payment_id or "").strip()
            if order.payment_id != normalized_payment_id:
                order.payment_id = normalized_payment_id
                update_fields.append("payment_id")

        if payment_metadata is not None and order.payment_metadata != payment_metadata:
            order.payment_metadata = payment_metadata
            update_fields.append("payment_metadata")

        if status_value is not None and order.status != status_value:
            order.status = status_value
            update_fields.append("status")

        if update_fields:
            order.save(update_fields=list(dict.fromkeys(update_fields)))

    def _set_refund_state(
        self,
        order,
        *,
        refund_status: str,
        refund_provider: str = "",
        refund_id: str = "",
        refund_error: str = "",
        refund_amount=None,
        refunded_at=None,
        payment_metadata: dict | None = None,
    ) -> None:
        order.refund_status = refund_status
        order.refund_provider = refund_provider
        order.refund_id = refund_id
        order.refund_error = refund_error
        order.refund_amount = refund_amount if refund_amount is not None else Decimal("0.00")
        order.refunded_at = refunded_at
        if payment_metadata is not None:
            order.payment_metadata = payment_metadata
        order.save(
            update_fields=[
                "refund_status",
                "refund_provider",
                "refund_id",
                "refund_error",
                "refund_amount",
                "refunded_at",
                "payment_metadata",
            ]
        )

    def _resolve_paid_payment_details(self, order, payment_method: str, payment_id: str, payment_metadata: dict):
        normalized_method = self._normalized_payment_method(payment_method)
        normalized_payment_id = str(payment_id or "").strip()

        if normalized_method in ("card", "google_pay", "klarna", "afterpay_clearpay"):
            resolved_metadata = get_stripe_payment_details(
                payment_id=normalized_payment_id,
                payment_metadata=payment_metadata,
            )
            payment_status = self._normalized_payment_method(resolved_metadata.get("stripe_payment_status"))
            if payment_status and payment_status != "paid":
                raise PaymentProviderError("Stripe checkout session is not paid yet")
            resolved_payment_id = (
                str(resolved_metadata.get("stripe_checkout_session_id") or "").strip()
                or normalized_payment_id
            )
            return resolved_payment_id, resolved_metadata

        if normalized_method == "paypal":
            resolved_metadata = resolve_paypal_payment_details(
                payment_id=normalized_payment_id,
                payment_metadata=payment_metadata,
            )
            capture_status = self._normalized_payment_method(resolved_metadata.get("paypal_capture_status"))
            if capture_status and capture_status != "completed":
                raise PaymentProviderError("PayPal payment is not completed yet")
            if not capture_status and not resolved_metadata.get("paypal_order_id"):
                raise PaymentProviderError("PayPal payment could not be verified")
            resolved_payment_id = (
                str(resolved_metadata.get("paypal_capture_id") or "").strip()
                or normalized_payment_id
            )
            return resolved_payment_id, resolved_metadata

        return normalized_payment_id, payment_metadata

    def _process_refund_for_cancellation(self, order) -> None:
        normalized_method = self._normalized_payment_method(order.payment_method)
        payment_metadata = dict(order.payment_metadata or {})

        if normalized_method in ("card", "google_pay", "klarna", "afterpay_clearpay"):
            try:
                refund_result = refund_stripe_payment(
                    order_id=order.id,
                    payment_id=order.payment_id,
                    payment_metadata=payment_metadata,
                )
            except PaymentProviderError as exc:
                self._set_refund_state(
                    order,
                    refund_status="failed",
                    refund_provider="stripe",
                    refund_error=str(exc),
                    refund_amount=Decimal("0.00"),
                    refunded_at=None,
                    payment_metadata=payment_metadata,
                )
                return

            refund_status = refund_result.get("status") or "failed"
            refund_provider = refund_result.get("provider") or "stripe"
            resolved_payment_metadata = refund_result.get("payment_metadata") or payment_metadata
            if refund_status == "succeeded":
                self._set_refund_state(
                    order,
                    refund_status="succeeded",
                    refund_provider=refund_provider,
                    refund_id=str(refund_result.get("refund_id") or "").strip(),
                    refund_error="",
                    refund_amount=order.total_amount,
                    refunded_at=timezone.now(),
                    payment_metadata=resolved_payment_metadata,
                )
                return

            self._set_refund_state(
                order,
                refund_status="not_required",
                refund_provider=refund_provider,
                refund_error="",
                refund_amount=Decimal("0.00"),
                refunded_at=None,
                payment_metadata=resolved_payment_metadata,
            )
            return

        if normalized_method == "paypal":
            try:
                refund_result = refund_paypal_payment(
                    order_id=order.id,
                    payment_id=order.payment_id,
                    payment_metadata=payment_metadata,
                )
            except PaymentProviderError as exc:
                self._set_refund_state(
                    order,
                    refund_status="failed",
                    refund_provider="paypal",
                    refund_error=str(exc),
                    refund_amount=Decimal("0.00"),
                    refunded_at=None,
                    payment_metadata=payment_metadata,
                )
                return

            self._set_refund_state(
                order,
                refund_status="succeeded",
                refund_provider=str(refund_result.get("provider") or "paypal"),
                refund_id=str(refund_result.get("refund_id") or "").strip(),
                refund_error="",
                refund_amount=order.total_amount,
                refunded_at=timezone.now(),
                payment_metadata=refund_result.get("payment_metadata") or payment_metadata,
            )
            return

        self._set_refund_state(
            order,
            refund_status="not_required",
            refund_provider="",
            refund_error="",
            refund_amount=Decimal("0.00"),
            refunded_at=None,
            payment_metadata=payment_metadata,
        )

    def _normalize_order_text_fields(self, data) -> None:
        text_fields = (
            "first_name",
            "last_name",
            "email",
            "phone",
            "alternative_phone",
            "address",
            "city",
            "postal_code",
            "floor_number",
            "payment_method",
            "payment_id",
            "special_notes",
        )
        for field in text_fields:
            if field in data:
                data[field] = str(data.get(field, "") or "").strip()

    def _strip_public_managed_fields(self, data) -> None:
        for field in (
            "status",
            "payment_id",
            "payment_metadata",
            "refund_status",
            "refund_provider",
            "refund_id",
            "refund_error",
            "refund_amount",
            "refunded_at",
            "cancelled_at",
            "send_confirmation_email",
            "user",
        ):
            data.pop(field, None)

    def _current_or_incoming_value(self, data, field: str, existing_order=None, default=""):
        if field in data:
            return data.get(field, default)
        if existing_order is not None:
            return getattr(existing_order, field, default)
        return default

    def _validate_public_order_fields(self, data, existing_order=None) -> None:
        required_fields = {
            "first_name": "First name is required",
            "last_name": "Last name is required",
            "email": "Email is required",
            "phone": "Phone number is required",
            "address": "Address is required",
            "city": "City is required",
            "postal_code": "Postal code is required",
        }
        errors = {
            field: message
            for field, message in required_fields.items()
            if not str(self._current_or_incoming_value(data, field, existing_order, "") or "").strip()
        }
        if errors:
            raise ValidationError(errors)

    def _existing_items_payload(self, order):
        return [
            {
                "product_id": item.product_id,
                "quantity": item.quantity,
                "price": item.price,
            }
            for item in order.items.all()
        ]

    def _prepare_order_data(self, data, items, *, existing_order=None, is_admin_request=False):
        self._normalize_order_text_fields(data)

        if not is_admin_request:
            self._validate_public_order_fields(data, existing_order)

        effective_items = items if items is not None else (self._existing_items_payload(existing_order) if existing_order else [])
        if not effective_items:
            raise ValidationError({"items": "At least one order item is required"})

        delivery_charges = _round_money(
            _as_decimal(self._current_or_incoming_value(data, "delivery_charges", existing_order, 0))
        )
        subtotal = Decimal("0.00")
        for item in effective_items:
            quantity = max(int(item.get("quantity", 1) or 1), 1)
            subtotal += _round_money(_as_decimal(item.get("price")) * quantity)

        promo_code = str(self._current_or_incoming_value(data, "promo_code", existing_order, "") or "").strip()
        promo_name = str(getattr(existing_order, "promo_name", "") or "")
        promo_discount_amount = _round_money(_as_decimal(getattr(existing_order, "promo_discount_amount", 0)))
        if promo_code:
            promo_result = _build_promotion_result(code=promo_code, items_payload=effective_items)
            promo_name = promo_result["promotion"].name
            promo_discount_amount = promo_result["discount_amount"]
        else:
            promo_name = ""
            promo_discount_amount = Decimal("0.00")

        total_amount = _round_money(subtotal + delivery_charges - promo_discount_amount)
        data["total_amount"] = str(total_amount)
        data["delivery_charges"] = str(delivery_charges)
        data["promo_code"] = promo_code.upper()
        data["promo_name"] = promo_name
        data["promo_discount_amount"] = str(promo_discount_amount)
        return effective_items

    def _replace_order_items(self, order, items) -> None:
        order.items.all().delete()
        for item in items:
            OrderItem.objects.create(
                order=order,
                product_id=item.get("product_id"),
                quantity=item.get("quantity"),
                price=item.get("price"),
                size=item.get("size", ""),
                color=item.get("color", ""),
                style=item.get("style", ""),
                dimension=item.get("dimension", ""),
                dimension_details=item.get("dimension_details", ""),
                selected_variants=item.get("selected_variants", {}),
                extras_total=item.get("extras_total", 0),
                include_dimension=bool(item.get("include_dimension", True)),
                assembly_service_selected=bool(item.get("assembly_service_selected", False)),
                assembly_service_price=item.get("assembly_service_price", 0),
            )

    def _build_order_reference_image_response(self, request, file_obj):
        content_type = str(getattr(file_obj, "content_type", "") or "").lower()
        if not content_type.startswith("image/"):
            raise ValidationError({"file": "Only image uploads are allowed"})
        if getattr(file_obj, "size", 0) > self.ORDER_REFERENCE_IMAGE_MAX_SIZE:
            raise ValidationError({"file": "Reference images must be 10MB or smaller"})

        base_name, ext = os.path.splitext(file_obj.name or "")
        if not ext:
            ext = ".jpg"
        safe_base = slugify(base_name) or "reference-image"
        file_name = f"orders/reference-images/{uuid.uuid4().hex}-{safe_base}{ext.lower()}"
        saved_path = default_storage.save(file_name, file_obj)
        stored_url = default_storage.url(saved_path)
        absolute_url = (
            stored_url
            if str(stored_url).startswith(("http://", "https://"))
            else request.build_absolute_uri(stored_url)
        )
        return {
            "url": absolute_url,
            "name": file_obj.name or "",
            "mime_type": content_type,
        }

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        items = data.pop("items", [])
        is_admin_request = self._is_admin_request(request)
        send_confirmation_email = True

        if is_admin_request:
            send_confirmation_email = _coerce_bool(data.pop("send_confirmation_email", None), default=True)
        else:
            self._strip_public_managed_fields(data)
            data["status"] = "pending"
            data["payment_id"] = ""
            send_confirmation_email = _should_confirm_public_order_on_create(data.get("payment_method"))

        items = self._prepare_order_data(data, items, is_admin_request=is_admin_request)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save(user=request.user if request.user.is_authenticated else None)

        self._replace_order_items(order, items)
        self._sync_cancelled_timestamp(order)

        if send_confirmation_email:
            _queue_order_confirmation_email(order)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        order = self.get_object()
        data = request.data.copy()
        items = data.pop("items", None)
        is_admin_request = self._is_admin_request(request)

        if not is_admin_request:
            self._strip_public_managed_fields(data)
            data.pop("payment_method", None)

        prepared_items = self._prepare_order_data(
            data,
            items,
            existing_order=order,
            is_admin_request=is_admin_request,
        )
        serializer = self.get_serializer(order, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            order = serializer.save()
            if items is not None:
                self._replace_order_items(order, prepared_items)
            self._sync_cancelled_timestamp(order)

        return Response(OrderSerializer(order).data)

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    @action(detail=False, methods=["post"], permission_classes=[AllowAny], url_path="upload_reference_image")
    def upload_reference_image(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            raise ValidationError({"file": "An image file is required"})
        return Response(self._build_order_reference_image_response(request, file_obj), status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def lookup(self, request):
        order_id = request.data.get("order_id")
        if not order_id:
            raise ValidationError({"order_id": "Order ID is required"})

        order = self._get_order_by_id(order_id)
        self._require_order_access(
            request,
            order,
            allow_email_match=True,
            error_message="Order details did not match our records",
        )
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def mark_paid(self, request, pk=None):
        order = self._get_order_by_id(pk)
        self._require_order_access(
            request,
            order,
            allow_email_match=True,
            error_message="We couldn't verify this payment update request",
        )
        payment_method = request.data.get("payment_method") or order.payment_method
        payment_id = request.data.get("payment_id") or order.payment_id
        payment_metadata = self._merge_payment_metadata(order, request.data.get("payment_metadata"))

        try:
            resolved_payment_id, payment_metadata = self._resolve_paid_payment_details(
                order,
                payment_method=str(payment_method or "").strip(),
                payment_id=str(payment_id or "").strip(),
                payment_metadata=payment_metadata,
            )
        except PaymentProviderError as exc:
            raise ValidationError({"payment_id": str(exc)}) from exc

        self._apply_payment_details(
            order,
            payment_method=str(payment_method or "").strip(),
            payment_id=resolved_payment_id,
            payment_metadata=payment_metadata,
            status_value="paid",
        )
        self._sync_cancelled_timestamp(order)
        _queue_order_confirmation_email(order)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def mark_shipped(self, request, pk=None):
        order = self.get_object()
        order.status = "shipped"
        order.save(update_fields=["status"])
        self._sync_cancelled_timestamp(order)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def mark_delivered(self, request, pk=None):
        order = self.get_object()
        order.status = "delivered"
        order.save(update_fields=["status"])
        self._sync_cancelled_timestamp(order)
        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["post"])
    def mark_cancelled(self, request, pk=None):
        order = self.get_object()

        if order.status in ("delivered", "shipped"):
            raise ValidationError(
                {"status": "Delivered orders cannot be cancelled or refunded."}
            )

        if order.status != "cancelled":
            with transaction.atomic():
                order.status = "cancelled"
                order.cancelled_at = timezone.now()
                order.save(update_fields=["status", "cancelled_at"])
                self._process_refund_for_cancellation(order)
                transaction.on_commit(lambda: send_order_cancellation_emails(order.id))
        else:
            if not order.cancelled_at:
                order.cancelled_at = timezone.now()
                order.save(update_fields=["cancelled_at"])
            if order.refund_status != "succeeded":
                self._process_refund_for_cancellation(order)

        return Response(OrderSerializer(order).data)

    @action(detail=True, methods=["get"], permission_classes=[IsAdminUser])
    def delivery_note_pdf(self, request, pk=None):
        order = (
            Order.objects.prefetch_related("items__product")
            .get(pk=pk)
        )
        pdf_bytes = build_delivery_note_pdf(order)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="order-{order.id}-delivery-note.pdf"'
        return response


class ReviewViewSet(viewsets.ModelViewSet):
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
    REVIEW_MEDIA_MAX_SIZE = int(getattr(settings, "REVIEW_MEDIA_MAX_UPLOAD_SIZE", 50 * 1024 * 1024))

    def get_permissions(self):
        if self.action in ("list", "retrieve", "create", "upload_media"):
            return [AllowAny()]
        return [IsAdminUser()]

    def _build_review_media_response(self, request, file_obj):
        content_type = str(getattr(file_obj, "content_type", "") or "").lower()
        if not (content_type.startswith("image/") or content_type.startswith("video/")):
            raise ValidationError({"file": "Only image and video uploads are allowed"})
        if getattr(file_obj, "size", 0) > self.REVIEW_MEDIA_MAX_SIZE:
            raise ValidationError({"file": "Review media must be 50MB or smaller"})

        media_type = "video" if content_type.startswith("video/") else "image"
        base_name, ext = os.path.splitext(file_obj.name or "")
        if not ext:
            ext = ".mp4" if media_type == "video" else ".jpg"
        safe_base = slugify(base_name) or "review-media"
        file_name = f"review-media/{uuid.uuid4().hex}-{safe_base}{ext.lower()}"
        saved_path = default_storage.save(file_name, file_obj)
        stored_url = default_storage.url(saved_path)
        absolute_url = stored_url if str(stored_url).startswith(("http://", "https://")) else request.build_absolute_uri(stored_url)
        return {
            "url": absolute_url,
            "type": media_type,
            "name": file_obj.name or "",
            "mime_type": content_type,
        }

    def get_queryset(self):
        queryset = Review.objects.select_related("product", "created_by").all().order_by("-created_at")
        product_id = self.request.query_params.get("product")
        product_slug = self.request.query_params.get("product_slug")
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        if product_slug:
            queryset = queryset.filter(product__slug=product_slug)

        if not (self.request.user and self.request.user.is_staff):
            queryset = queryset.filter(is_visible=True)
        return queryset

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        is_admin = bool(request.user and request.user.is_staff)

        # Frontend submissions should always start hidden
        if not is_admin:
            data["is_visible"] = False
        else:
            # Default admin submissions to visible unless explicitly set
            data.setdefault("is_visible", True)

        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=request.user if request.user.is_authenticated else None)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def perform_update(self, serializer):
        serializer.save()

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        # Non-admin users should not see hidden reviews
        if not (request.user and request.user.is_staff) and not instance.is_visible:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], permission_classes=[AllowAny])
    def upload_media(self, request):
        file_obj = request.FILES.get("file")
        if not file_obj:
            raise ValidationError({"file": "A media file is required"})
        return Response(self._build_review_media_response(request, file_obj), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    def set_visibility(self, request, pk=None):
        review = self.get_object()
        raw_value = request.data.get("is_visible")
        if isinstance(raw_value, str):
            raw_value = raw_value.lower().strip()
            is_visible = raw_value in ("1", "true", "yes", "on")
        else:
            is_visible = bool(raw_value)
        review.is_visible = is_visible
        review.save(update_fields=["is_visible"])
        return Response(self.get_serializer(review).data)


class UploadViewSet(viewsets.ViewSet):
    permission_classes = [IsAdminUser]

    def _extract_public_url(self, value):
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in ("url", "publicUrl", "publicURL", "signedUrl", "signedURL"):
                candidate = value.get(key)
                if isinstance(candidate, str):
                    return candidate
            data = value.get("data")
            if isinstance(data, dict):
                for key in ("url", "publicUrl", "publicURL", "signedUrl", "signedURL"):
                    candidate = data.get(key)
                    if isinstance(candidate, str):
                        return candidate
        if hasattr(value, "data") and isinstance(value.data, dict):
            for key in ("url", "publicUrl", "publicURL", "signedUrl", "signedURL"):
                candidate = value.data.get(key)
                if isinstance(candidate, str):
                    return candidate
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            if isinstance(dumped, dict):
                return self._extract_public_url(dumped)
        return None

    def create(self, request):
        if "file" not in request.FILES:
            return Response({"error": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        file_obj = request.FILES["file"]
        base_name, ext = os.path.splitext(file_obj.name or "")
        safe_base = slugify(base_name) or "upload"
        safe_ext = (ext or "").lower()
        file_name = f"{uuid.uuid4().hex}-{safe_base}{safe_ext}"

        # Save to local media storage (Render disk)
        saved_path = default_storage.save(file_name, file_obj)
        relative_url = default_storage.url(saved_path)
        absolute_url = request.build_absolute_uri(relative_url)

        # Return absolute URL so frontend stores a fully-qualified backend path
        return Response({"url": absolute_url, "relative_url": relative_url})


class PaymentViewSet(viewsets.ViewSet):
    def _paypal_request(self, method: str, path: str, **kwargs):
        return paypal_request(method, path, **kwargs)

    @action(detail=False, methods=["post"])
    def create_stripe_session(self, request):
        stripe.api_key = settings.STRIPE_SECRET_KEY
        if not stripe.api_key:
            return Response(
                {"error": "Stripe is not configured. Please set STRIPE_SECRET_KEY."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        items = request.data.get("items", [])
        line_items = []
        for item in items:
            line_items.append(
                {
                    "price_data": {
                        "currency": request.data.get("currency", "gbp"),
                        "product_data": {"name": item["name"]},
                        "unit_amount": int(float(item["price"]) * 100),
                    },
                    "quantity": item["quantity"],
                }
            )

        if not line_items:
            return Response({"error": "No line items provided for checkout."}, status=status.HTTP_400_BAD_REQUEST)

        delivery_charges = request.data.get("delivery_charges", 0)
        if float(delivery_charges) > 0:
            line_items.append(
                {
                    "price_data": {
                        "currency": request.data.get("currency", "gbp"),
                        "product_data": {"name": "Delivery Charges"},
                        "unit_amount": int(float(delivery_charges) * 100),
                    },
                    "quantity": 1,
                }
            )

        order_id = request.data.get("order_id")
        metadata = {}
        if order_id:
            metadata["order_id"] = str(order_id)
        requested_payment_method = str(request.data.get("payment_method") or "card").strip().lower()
        stripe_payment_method_types = {
            "card": ["card"],
            "google_pay": ["card"],
            "klarna": ["klarna"],
            "afterpay_clearpay": ["afterpay_clearpay"],
        }.get(requested_payment_method)
        if not stripe_payment_method_types:
            return Response(
                {"error": "Unsupported Stripe payment method."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        metadata["requested_payment_method"] = requested_payment_method

        success_url = request.data.get("success_url")
        if success_url and "{CHECKOUT_SESSION_ID}" not in success_url:
            separator = "&" if "?" in success_url else "?"
            success_url = f"{success_url}{separator}session_id={{CHECKOUT_SESSION_ID}}"

        try:
            session_kwargs = {
                "payment_method_types": stripe_payment_method_types,
                "line_items": line_items,
                "mode": "payment",
                "success_url": success_url,
                "cancel_url": request.data.get("cancel_url"),
                "client_reference_id": str(order_id) if order_id else None,
            }
            if metadata:
                session_kwargs["metadata"] = metadata
                session_kwargs["payment_intent_data"] = {"metadata": metadata}

            checkout_session = stripe.checkout.Session.create(
                **session_kwargs,
            )
            if order_id:
                order = Order.objects.filter(pk=order_id).first()
                if order:
                    payment_metadata = dict(order.payment_metadata or {})
                    payment_metadata["stripe_checkout_session_id"] = checkout_session.id
                    payment_metadata["requested_payment_method"] = requested_payment_method
                    order.payment_id = checkout_session.id
                    order.payment_method = requested_payment_method
                    order.payment_metadata = payment_metadata
                    order.save(update_fields=["payment_id", "payment_method", "payment_metadata"])
            return Response({"id": checkout_session.id, "url": checkout_session.url})
        except Exception as exc:  # pragma: no cover - external service
            # Surface the error to the client so they see why checkout failed
            body = {"error": str(exc)}
            code = getattr(exc, "code", None)
            if code:
                body["code"] = code
            return Response(body, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=["get"])
    def stripe_config(self, request):
        """
        Provide publishable key to the frontend at runtime so it need not live in the frontend .env.
        """
        if not settings.STRIPE_PUBLISHABLE_KEY:
            return Response({"error": "Stripe publishable key not set"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"publishableKey": settings.STRIPE_PUBLISHABLE_KEY})

    @action(detail=False, methods=["post"])
    def create_paypal_order(self, request):
        access_token, auth_error = self._paypal_access_token()
        if not access_token:
            return Response(auth_error or {"error": "PayPal auth failed"}, status=status.HTTP_400_BAD_REQUEST)

        total = request.data.get("total")
        currency = request.data.get("currency", "GBP")
        return_url = request.data.get("return_url")
        cancel_url = request.data.get("cancel_url")
        order_id = request.data.get("order_id")
        payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "amount": {"currency_code": currency, "value": str(total)},
                    "custom_id": str(order_id) if order_id else None,
                }
            ],
        }
        if return_url and cancel_url:
            payload["application_context"] = {"return_url": return_url, "cancel_url": cancel_url}

        response, error = self._paypal_request(
            "POST",
            "/v2/checkout/orders",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json=payload,
        )
        if error:
            return Response(error, status=status.HTTP_400_BAD_REQUEST)
        payload = response.json()
        paypal_order_id = str(payload.get("id") or "").strip()
        if order_id and paypal_order_id:
            order = Order.objects.filter(pk=order_id).first()
            if order:
                payment_metadata = dict(order.payment_metadata or {})
                payment_metadata["paypal_order_id"] = paypal_order_id
                order.payment_id = paypal_order_id
                order.payment_method = "paypal"
                order.payment_metadata = payment_metadata
                order.save(update_fields=["payment_id", "payment_method", "payment_metadata"])
        return Response(payload)

    @action(detail=False, methods=["post"])
    def capture_paypal_order(self, request):
        access_token, auth_error = self._paypal_access_token()
        if not access_token:
            return Response(auth_error or {"error": "PayPal auth failed"}, status=status.HTTP_400_BAD_REQUEST)
        order_id = request.data.get("orderID")
        response, error = self._paypal_request(
            "POST",
            f"/v2/checkout/orders/{order_id}/capture",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        )
        if error:
            return Response(error, status=status.HTTP_400_BAD_REQUEST)
        payload = response.json()
        capture = extract_paypal_capture(payload)
        capture_id = extract_paypal_capture_id(payload)
        capture_status = str(capture.get("status") or "").strip()
        if not capture_id or capture_status.lower() != "completed":
            return Response(payload)
        local_order_id = extract_local_order_id_from_paypal(payload)
        if local_order_id:
            order = Order.objects.filter(pk=local_order_id).first()
            if order:
                payment_metadata = dict(order.payment_metadata or {})
                payment_metadata["paypal_order_id"] = str(order_id or "").strip()
                if capture_id:
                    payment_metadata["paypal_capture_id"] = capture_id
                if capture_status:
                    payment_metadata["paypal_capture_status"] = capture_status
                order.payment_method = "paypal"
                order.payment_id = capture_id or str(order_id or "").strip()
                order.payment_metadata = payment_metadata
                order.status = "paid"
                order.save(update_fields=["payment_method", "payment_id", "payment_metadata", "status"])
                _queue_order_confirmation_email(order)
        return Response(payload)

    def _paypal_access_token(self):
        return paypal_access_token()


class FilterTypeViewSet(viewsets.ModelViewSet):
    """ViewSet for managing filter types"""
    queryset = FilterType.objects.all().order_by('display_order', 'name')
    serializer_class = FilterTypeSerializer
    permission_classes = [IsAdminOrReadOnly]


class FilterOptionViewSet(viewsets.ModelViewSet):
    """CRUD for individual filter options (e.g., King, Double)."""
    queryset = FilterOption.objects.all().select_related("filter_type").order_by("display_order", "name")
    serializer_class = FilterOptionSerializer
    permission_classes = [IsAdminOrReadOnly]

    @action(detail=True, methods=["get", "patch"], url_path="products")
    def products(self, request, pk=None):
        if not request.user or not request.user.is_staff:
            return Response({"detail": "Admin access required."}, status=status.HTTP_403_FORBIDDEN)

        option = self.get_object()

        if request.method == "GET":
            assigned_products = (
                Product.objects.filter(filter_values__filter_option=option)
                .select_related("category", "subcategory")
                .order_by("sort_order", "-created_at")
                .distinct()
            )
            assigned_product_ids = list(assigned_products.values_list("id", flat=True))
            return Response(
                {
                    "filter_option": option.id,
                    "assigned_product_ids": assigned_product_ids,
                    "assigned_products": ProductAdminListSerializer(assigned_products, many=True).data,
                }
            )

        raw_product_ids = request.data.get("product_ids", [])
        if not isinstance(raw_product_ids, list):
            raise ValidationError({"product_ids": "Expected a list of product IDs."})

        normalized_ids = []
        for raw_id in raw_product_ids:
            try:
                product_id = int(raw_id)
            except (TypeError, ValueError):
                raise ValidationError({"product_ids": "Product IDs must be numbers."})
            if product_id > 0 and product_id not in normalized_ids:
                normalized_ids.append(product_id)

        valid_ids = set(Product.objects.filter(id__in=normalized_ids).values_list("id", flat=True))
        if len(valid_ids) != len(normalized_ids):
            raise ValidationError({"product_ids": "One or more products do not exist."})

        with transaction.atomic():
            ProductFilterValue.objects.filter(filter_option=option).exclude(product_id__in=normalized_ids).delete()
            existing_ids = set(
                ProductFilterValue.objects.filter(filter_option=option, product_id__in=normalized_ids).values_list(
                    "product_id", flat=True
                )
            )
            ProductFilterValue.objects.bulk_create(
                [
                    ProductFilterValue(product_id=product_id, filter_option=option)
                    for product_id in normalized_ids
                    if product_id not in existing_ids
                ],
                ignore_conflicts=True,
            )
        cache.clear()
        assigned_products = (
            Product.objects.filter(id__in=normalized_ids)
            .select_related("category", "subcategory")
            .order_by("sort_order", "-created_at")
        )
        return Response(
            {
                "filter_option": option.id,
                "assigned_product_ids": normalized_ids,
                "assigned_products": ProductAdminListSerializer(assigned_products, many=True).data,
            }
        )

    def perform_create(self, serializer):
        serializer.save()
        cache.clear()

    def perform_update(self, serializer):
        serializer.save()
        cache.clear()

    def perform_destroy(self, instance):
        instance.delete()
        cache.clear()


class DimensionTemplateViewSet(viewsets.ModelViewSet):
    queryset = DimensionTemplate.objects.all().order_by("name")
    serializer_class = FilterTypeSerializer  # placeholder, set below
    permission_classes = [IsAdminOrReadOnly]

    def get_serializer_class(self):
        from .serializers import DimensionTemplateSerializer
        return DimensionTemplateSerializer


class CategoryFilterViewSet(viewsets.ModelViewSet):
    """
    Manage which filter types appear on a given category or subcategory page.
    """
    queryset = CategoryFilter.objects.select_related(
        "filter_type", "category", "subcategory"
    ).order_by("display_order", "id")
    serializer_class = CategoryFilterSerializer
    permission_classes = [IsAdminOrReadOnly]

    def get_queryset(self):
        queryset = CategoryFilter.objects.select_related(
            "filter_type", "category", "subcategory"
        ).order_by("display_order", "id")

        category_id = self.request.query_params.get("category")
        subcategory_id = self.request.query_params.get("subcategory")

        if category_id:
            queryset = queryset.filter(category_id=category_id)
        if subcategory_id:
            queryset = queryset.filter(subcategory_id=subcategory_id)

        return queryset

    def perform_create(self, serializer):
        serializer.save()
        cache.clear()

    def perform_update(self, serializer):
        serializer.save()
        cache.clear()

    def perform_destroy(self, instance):
        instance.delete()
        cache.clear()


class CategoryFiltersView(generics.GenericAPIView):
    """
    GET /api/categories/{category_slug}/filters/
    Returns all available filters for a category with product counts
    """
    permission_classes = [AllowAny]
    
    @method_decorator(cache_page(CATEGORY_FILTER_CACHE_TTL))
    def get(self, request, category_slug):
        from django.db.models import Q, Count
        
        # Get the category
        try:
            category = Category.objects.get(slug=category_slug)
        except Category.DoesNotExist:
            return Response({"error": "Category not found"}, status=status.HTTP_404_NOT_FOUND)

        # Optional subcategory context (by slug)
        sub_slug = request.query_params.get("subcategory")
        subcategory = None
        if sub_slug:
            subcategory = (
                SubCategory.objects.filter(slug=sub_slug)
                .filter(Q(category=category) | Q(additional_categories=category))
                .distinct()
                .first()
            )
        
        # Get filters linked to this category
        category_filters = CategoryFilter.objects.filter(
            Q(category=category) | Q(subcategory__category=category) | Q(subcategory__additional_categories=category),
            is_active=True
        ).distinct()

        # If subcategory is specified, prefer filters tied to it but still include category-level ones
        if subcategory:
            category_filters = category_filters.filter(Q(subcategory=subcategory) | Q(category=category))

        category_filters = category_filters.select_related('filter_type').prefetch_related(
            Prefetch(
                'filter_type__options',
                queryset=FilterOption.objects.filter(is_active=True).order_by('display_order', 'name'),
                to_attr='active_options',
            )
        ).order_by('display_order')
        
        # Collect unique filter types
        filter_types = []
        seen_ids = set()
        for cf in category_filters:
            ft = cf.filter_type
            if ft.is_active and ft.id not in seen_ids:
                ft.category_option_order = list(cf.option_order or [])
                filter_types.append(ft)
                seen_ids.add(ft.id)

        # Precompute product counts in one query (vs per-option loop)
        base_filter_qs = ProductFilterValue.objects.filter(
            Q(product__category=category)
            | Q(product__subcategory__category=category)
            | Q(product__subcategory__additional_categories=category),
            product__in_stock=True,
            **({"product__subcategory": subcategory} if subcategory else {}),
        ).distinct()
        option_counts = base_filter_qs.values("filter_option").annotate(product_count=Count("product", distinct=True))
        count_lookup = {row["filter_option"]: row["product_count"] for row in option_counts}
        
        # Attach counts without extra queries
        for ft in filter_types:
            options = ft.active_options if hasattr(ft, "active_options") else ft.options.filter(is_active=True)
            for option in options:
                option.product_count = count_lookup.get(option.id, 0)
        
        serializer = FilterTypeSerializer(filter_types, many=True)
        return Response({'filters': serializer.data})


class ProductFiltersView(generics.GenericAPIView):
    """
    GET /api/products/filters/?category=beds&subcategory=dining-tables
    Returns cached product filters separately from paginated product cards.
    """
    permission_classes = [AllowAny]

    @method_decorator(cache_page(CATEGORY_FILTER_CACHE_TTL))
    def get(self, request):
        from django.db.models import Q, Count

        previous_force_debug_cursor = connection.force_debug_cursor
        if PRODUCT_SQL_DEBUG_LOG:
            connection.force_debug_cursor = True
        query_start = len(connection.queries) if PRODUCT_SQL_DEBUG_LOG else 0
        request_start = time.perf_counter()
        filters_start = time.perf_counter()
        category_slug = (request.query_params.get("category") or "").strip()
        subcategory_slug = (request.query_params.get("subcategory") or "").strip()

        category = None
        subcategory = None
        if subcategory_slug:
            subcategory = SubCategory.objects.select_related("category").filter(slug=subcategory_slug).first()
            if not subcategory:
                if PRODUCT_SQL_DEBUG_LOG:
                    connection.force_debug_cursor = previous_force_debug_cursor
                return Response({"filters": []})
            category = subcategory.category

        if category_slug:
            try:
                category = Category.objects.get(slug=category_slug)
            except Category.DoesNotExist:
                if not subcategory:
                    if PRODUCT_SQL_DEBUG_LOG:
                        connection.force_debug_cursor = previous_force_debug_cursor
                    return Response({"filters": []})

        if not category and not subcategory:
            if PRODUCT_SQL_DEBUG_LOG:
                connection.force_debug_cursor = previous_force_debug_cursor
            return Response({"filters": []})

        category_filters = CategoryFilter.objects.filter(is_active=True)
        if subcategory:
            category_filters = category_filters.filter(Q(subcategory=subcategory) | Q(category=category))
        else:
            category_filters = category_filters.filter(
                Q(category=category) | Q(subcategory__category=category) | Q(subcategory__additional_categories=category)
            )

        category_filters = category_filters.select_related("filter_type").prefetch_related(
            Prefetch(
                "filter_type__options",
                queryset=FilterOption.objects.filter(is_active=True).order_by("display_order", "name"),
                to_attr="active_options",
            )
        ).distinct().order_by("display_order")

        filter_types = []
        seen_ids = set()
        for cf in category_filters:
            ft = cf.filter_type
            if ft.is_active and ft.id not in seen_ids:
                ft.category_option_order = list(cf.option_order or [])
                filter_types.append(ft)
                seen_ids.add(ft.id)

        product_scope = Q()
        if subcategory:
            product_scope &= Q(product__subcategory=subcategory)
        elif category:
            product_scope &= (
                Q(product__category=category)
                | Q(product__subcategory__category=category)
                | Q(product__subcategory__additional_categories=category)
            )

        base_filter_qs = ProductFilterValue.objects.filter(
            product_scope,
            product__in_stock=True,
            product__is_hidden=False,
            product__category__is_hidden=False,
        ).distinct()
        base_filter_qs = base_filter_qs.filter(Q(product__subcategory__isnull=True) | Q(product__subcategory__is_hidden=False))
        option_counts = base_filter_qs.values("filter_option").annotate(product_count=Count("product", distinct=True))
        count_lookup = {row["filter_option"]: row["product_count"] for row in option_counts}

        for ft in filter_types:
            for option in getattr(ft, "active_options", []):
                option.product_count = count_lookup.get(option.id, 0)

        serializer = FilterTypeSerializer(filter_types, many=True)
        response = Response({"filters": serializer.data})
        if PRODUCT_SQL_DEBUG_LOG:
            slowest = _slowest_sql_query(query_start)
            logger.debug(
                (
                    "ProductFiltersView path=%s status=%s total_ms=%.1f filters_generation_ms=%.1f "
                    "queries=%s slowest_sql_ms=%s slowest_sql=%s"
                ),
                request.get_full_path(),
                response.status_code,
                (time.perf_counter() - request_start) * 1000,
                (time.perf_counter() - filters_start) * 1000,
                len(connection.queries) - query_start,
                f"{slowest['time_ms']:.1f}" if slowest else "0.0",
                slowest["sql"] if slowest else "",
            )
            connection.force_debug_cursor = previous_force_debug_cursor
        return response


class ProductStyleLibraryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only list of all style groups across products, for reuse.
    """
    queryset = ProductStyle.objects.select_related("product", "size").all().order_by("product_id", "id")
    serializer_class = ProductStyleLibrarySerializer
    permission_classes = [AllowAny]
