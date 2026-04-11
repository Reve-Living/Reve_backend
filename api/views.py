import uuid
import os
import stripe
import requests
from requests.adapters import HTTPAdapter, Retry
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urljoin

from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.contrib.auth.models import User
from django.utils.text import slugify
from django.db import transaction
from django.db.models import Prefetch, Q, Case, When, Value, IntegerField
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.http import HttpResponse
from rest_framework import viewsets, status, generics
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError
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
)
from .emails import send_order_confirmation_emails
from .delivery_note_pdf import build_delivery_note_pdf


TWOPLACES = Decimal("0.01")


def _as_decimal(value, default="0.00"):
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


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

        orders_current = Order.objects.filter(created_at__gte=start_of_month)
        orders_prev = Order.objects.filter(created_at__gte=prev_month_start, created_at__lte=prev_month_end)

        total_revenue = float(
            Order.objects.aggregate(total=Sum("total_amount"))["total"] or 0
        )
        revenue_current = float(orders_current.aggregate(total=Sum("total_amount"))["total"] or 0)
        revenue_prev = float(orders_prev.aggregate(total=Sum("total_amount"))["total"] or 0)

        total_orders = Order.objects.count()
        total_products = Product.objects.count()

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
    queryset = Category.objects.all().prefetch_related("subcategories").order_by("sort_order", "name")
    serializer_class = CategorySerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

    def _invalidate_cache(self):
        """Ensure category changes are reflected immediately on the site."""
        from django.core.cache import cache

        cache.clear()

    @method_decorator(cache_page(60 * 5))
    def _cached_list(self, request, *args, **kwargs):
        """Public storefront reads can be cached briefly to reduce repeated load."""
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


class SubCategoryViewSet(viewsets.ModelViewSet):
    queryset = SubCategory.objects.all().order_by("sort_order", "name")
    serializer_class = SubCategorySerializer
    permission_classes = [IsAdminOrReadOnly]

    def _invalidate_cache(self):
        """Ensure category listings reflect subcategory changes immediately."""
        from django.core.cache import cache

        cache.clear()

    @method_decorator(cache_page(60 * 5))
    def _cached_list(self, request, *args, **kwargs):
        """Cache public storefront subcategory lists briefly."""
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
        category_id = self.request.query_params.get("category")
        if category_id:
            queryset = queryset.filter(category_id=category_id)
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


class CollectionViewSet(viewsets.ModelViewSet):
    queryset = Collection.objects.all().prefetch_related("products").order_by("sort_order", "name")
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

    # Prefetch groups tuned for list vs detail
    _list_prefetches = [
        "images",
        "sizes",
        Prefetch(
            "filter_values",
            queryset=ProductFilterValue.objects.select_related("filter_option__filter_type"),
            to_attr="filter_values_all",
        ),
    ]
    _detail_prefetches = _list_prefetches + [
        "videos",
        "colors",
        "styles",
        "fabrics",
        "mattresses",
        "dimension_template_link__template__rows",
    ]

    def _base_queryset(self):
        return Product.objects.select_related("category", "subcategory")

    def get_serializer_class(self):
        if self.action == "list" and not self.request.query_params.get("slug"):
            from .serializers import ProductListSerializer
            return ProductListSerializer
        if self.request.method in ("POST", "PUT", "PATCH"):
            return ProductWriteSerializer
        return ProductSerializer

    def get_queryset(self):
        # Choose a lighter prefetch set for list views (most traffic)
        is_list = self.action == "list" and not self.request.query_params.get("slug")
        prefetches = self._list_prefetches if is_list else self._detail_prefetches
        queryset = self._base_queryset().prefetch_related(*prefetches)
        is_admin_request = bool(self.request.user and self.request.user.is_authenticated and self.request.user.is_staff)
        if not is_admin_request:
            queryset = queryset.filter(is_hidden=False)

        category = self.request.query_params.get("category")
        subcategory = self.request.query_params.get("subcategory")
        bestseller = self.request.query_params.get("bestseller")
        is_new = self.request.query_params.get("is_new")
        slug = self.request.query_params.get("slug")
        
        if category:
            queryset = queryset.filter(category__slug=category)
        if subcategory:
            queryset = queryset.filter(subcategory__slug=subcategory)
        if bestseller:
            queryset = queryset.filter(is_bestseller=True)
        if is_new:
            queryset = queryset.filter(is_new=True)
        if slug:
            queryset = queryset.filter(slug=slug)
        
        # Apply dynamic filters from filter system
        filter_types = FilterType.objects.filter(is_active=True)
        for ft in filter_types:
            filter_values = self.request.query_params.get(ft.slug)
            if filter_values:
                option_slugs = filter_values.split(',')
                queryset = queryset.filter(
                    filter_values__filter_option__slug__in=option_slugs,
                    filter_values__filter_option__filter_type=ft
                ).distinct()
        
        return queryset.order_by("sort_order", "-created_at")

    def _invalidate_cache(self):
        """Drop cached product lists after admin changes."""
        from django.core.cache import cache

        cache.clear()

    @method_decorator(cache_page(60 * 5))
    def _cached_list(self, request, *args, **kwargs):
        """
        Cache public storefront product lists briefly so the first homepage/category
        visit does not have to rebuild the same payload every time.
        """
        return super().list(request, *args, **kwargs)

    def list(self, request, *args, **kwargs):
        """
        Keep admin traffic fully fresh while allowing public storefront reads to use
        short-lived caching.
        """
        has_auth_header = bool(request.headers.get("Authorization"))
        if getattr(request.user, "is_staff", False) or has_auth_header:
            return super().list(request, *args, **kwargs)
        return self._cached_list(request, *args, **kwargs)

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
        return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        data = request.data.copy()
        simple_visibility_update = set(data.keys()).issubset({"is_hidden"})

        if simple_visibility_update:
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            self._invalidate_cache()
            refreshed = self._base_queryset().prefetch_related(*self._detail_prefetches).get(pk=instance.pk)
            return Response(ProductSerializer(refreshed).data)

        images = data.pop("images", None)
        videos = data.pop("videos", None)
        colors = data.pop("colors", None)
        sizes = data.pop("sizes", None)
        styles = data.pop("styles", None)
        fabrics = data.pop("fabrics", None)
        mattresses = data.pop("mattresses", None)
        filter_values = data.pop("filter_values", None)

        images, videos, colors, sizes, styles, fabrics, mattresses = self._validate_related_data(
            images or [], videos or [], colors or [], sizes or [], styles or [], fabrics or [], mattresses or []
        )

        instance = self.get_object()
        serializer = self.get_serializer(instance, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        dimension_template_obj = serializer.validated_data.get("_dimension_template_obj")
        product = serializer.save()

        if images is not None:
            product.images.all().delete()
        if videos is not None:
            product.videos.all().delete()
        if colors is not None:
            product.colors.all().delete()
        if sizes is not None:
            product.sizes.all().delete()
        if styles is not None:
            product.styles.all().delete()
        if fabrics is not None:
            product.fabrics.all().delete()
        if mattresses is not None:
            product.mattresses.all().delete()

        self._handle_related_data(
            product,
            images or [],
            videos or [],
            colors or [],
            sizes or [],
            styles or [],
            fabrics or [],
            mattresses or [],
        )
        if filter_values is not None:
            self._handle_filter_values(product, filter_values)

        self._handle_dimension_template(product, dimension_template_obj)

        self._invalidate_cache()
        return Response(ProductSerializer(product).data)

    def _handle_related_data(self, product, images, videos, colors, sizes, styles, fabrics, mattresses):
        for img in images:
            ProductImage.objects.create(
                product=product,
                url=img.get("url"),
                color_name=img.get("color_name", ""),
                style_name=img.get("style_name", ""),
                alt_text=img.get("alt_text", ""),
            )
        for vid in videos:
            ProductVideo.objects.create(product=product, url=vid.get("url"))
        for col in colors:
            ProductColor.objects.create(
                product=product,
                name=col.get("name", ""),
                hex_code=col.get("hex_code", "#000000"),
                image_url=col.get("image_url", ""),
            )
        size_objs = []
        for size in sizes:
            size_obj = ProductSize.objects.create(
                product=product,
                name=size.get("name", ""),
                description=size.get("description", ""),
                price_delta=size.get("price_delta", 0),
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
            )

    def _validate_related_data(self, images, videos, colors, sizes, styles, fabrics, mattresses):
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
            cleaned_images.append({"url": url, "color_name": color_name, "style_name": style_name, "alt_text": alt_text})

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
            cleaned_colors.append({"name": name, "hex_code": hex_code, "image_url": image_url})

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
            cleaned_sizes.append({"name": value, "description": description, "price_delta": delta})

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
                colors_list.append({
                    "name": cname,
                    "hex_code": str(col.get("hex_code", "#000000")).strip() or "#000000",
                    "image_url": str(col.get("image_url", "")).strip(),
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

    queryset = MattressOption.objects.all().prefetch_related("prices")
    serializer_class = MattressOptionSerializer
    permission_classes = [IsAdminOrReadOnly]
    http_method_names = ["get", "post", "put", "patch", "delete", "head", "options"]

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

    def _upsert_prices(self, option, prices):
        option.prices.all().delete()
        for p in prices:
            MattressOptionPrice.objects.create(option=option, **p)

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        prices_raw = data.pop("prices", [])
        categories_raw = data.pop("categories", [])
        subcategories_raw = data.pop("subcategories", [])
        prices = self._clean_prices(prices_raw)
        categories = self._clean_categories(categories_raw)
        subcategories = self._clean_subcategories(subcategories_raw)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        option = serializer.save()
        self._upsert_prices(option, prices)
        if categories is not None:
            option.categories.set(categories)
        if subcategories is not None:
            option.subcategories.set(subcategories)
        return Response(self.get_serializer(option).data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        data = request.data.copy()
        prices_raw = data.pop("prices", None)
        categories_raw = data.pop("categories", None)
        subcategories_raw = data.pop("subcategories", None)
        prices = self._clean_prices(prices_raw) if prices_raw is not None else None
        categories = self._clean_categories(categories_raw) if categories_raw is not None else None
        subcategories = self._clean_subcategories(subcategories_raw) if subcategories_raw is not None else None
        serializer = self.get_serializer(instance, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        option = serializer.save()
        if prices is not None:
            self._upsert_prices(option, prices)
        if categories is not None:
            option.categories.set(categories)
        if subcategories is not None:
            option.subcategories.set(subcategories)
        return Response(self.get_serializer(option).data)


class ProductMattressAdminViewSet(viewsets.ModelViewSet):
    queryset = ProductMattress.objects.select_related(
        "product", "product__category", "product__subcategory", "source_product"
    ).order_by("product__name", "id")
    serializer_class = ProductMattressSerializer
    permission_classes = [IsAdminUser]
    http_method_names = ["get", "patch", "delete", "head", "options"]


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
    queryset = Order.objects.all().order_by("-created_at")
    serializer_class = OrderSerializer

    def get_permissions(self):
        if self.action == "create":
            return [AllowAny()]
        if self.request.user.is_staff:
            return [IsAdminUser()]
        return [IsAuthenticated()]

    def get_queryset(self):
        if self.request.user.is_staff:
            return super().get_queryset()
        return super().get_queryset().filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        data = request.data.copy()
        items = data.pop("items", [])
        delivery_charges = _round_money(_as_decimal(data.get("delivery_charges", 0)))
        subtotal = Decimal("0.00")
        for item in items:
            quantity = max(int(item.get("quantity", 1) or 1), 1)
            subtotal += _round_money(_as_decimal(item.get("price")) * quantity)

        promo_code = str(data.get("promo_code", "") or "").strip()
        promo_name = ""
        promo_discount_amount = Decimal("0.00")
        if promo_code:
            promo_result = _build_promotion_result(code=promo_code, items_payload=items)
            promo_name = promo_result["promotion"].name
            promo_discount_amount = promo_result["discount_amount"]

        total_amount = _round_money(subtotal + delivery_charges - promo_discount_amount)
        data["total_amount"] = str(total_amount)
        data["delivery_charges"] = str(delivery_charges)
        data["promo_code"] = promo_code.upper()
        data["promo_name"] = promo_name
        data["promo_discount_amount"] = str(promo_discount_amount)
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        order = serializer.save(user=request.user if request.user.is_authenticated else None)

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

        transaction.on_commit(lambda: send_order_confirmation_emails(order.id))
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def mark_paid(self, request, pk=None):
        order = self.get_object()
        payment_method = request.data.get("payment_method")
        payment_id = request.data.get("payment_id")
        if payment_method:
            order.payment_method = payment_method
        if payment_id:
            order.payment_id = payment_id
        order.status = "paid"
        order.save()
        return Response({"status": "order marked as paid", "payment_method": order.payment_method, "payment_id": order.payment_id})

    @action(detail=True, methods=["post"])
    def mark_shipped(self, request, pk=None):
        order = self.get_object()
        order.status = "shipped"
        order.save()
        return Response({"status": "order marked as shipped"})

    @action(detail=True, methods=["post"])
    def mark_delivered(self, request, pk=None):
        order = self.get_object()
        order.status = "delivered"
        order.save()
        return Response({"status": "order marked as delivered"})

    @action(detail=True, methods=["post"])
    def mark_cancelled(self, request, pk=None):
        order = self.get_object()
        order.status = "cancelled"
        order.save()
        return Response({"status": "order marked as cancelled"})

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

    def get_permissions(self):
        if self.action in ("list", "retrieve", "create"):
            return [AllowAny()]
        return [IsAdminUser()]

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
    def _paypal_session(self):
        # Shared session with retry/backoff to reduce transient PayPal failures
        if hasattr(self, "_paypal_cached_session"):
            return self._paypal_cached_session

        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(max_retries=retries)
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        self._paypal_cached_session = session
        return session

    def _paypal_request(self, method: str, path: str, **kwargs):
        # Apply sane defaults and user-friendly error messages
        timeout = kwargs.pop(
            "timeout",
            (
                getattr(settings, "PAYPAL_CONNECT_TIMEOUT", 5),
                getattr(settings, "PAYPAL_TIMEOUT", 15),
            ),
        )
        url = f"{settings.PAYPAL_BASE_URL}{path}"
        try:
            resp = self._paypal_session().request(method, url, timeout=timeout, **kwargs)
        except requests.Timeout:
            return None, {"error": "PayPal timed out. Please try again in a moment."}
        except requests.RequestException as exc:
            return None, {"error": f"PayPal request failed: {exc}"}

        if resp.status_code >= 400:
            return None, {
                "error": "PayPal returned an error",
                "status": resp.status_code,
                "body": resp.text,
            }
        return resp, None

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

        try:
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=line_items,
                mode="payment",
                success_url=request.data.get("success_url"),
                cancel_url=request.data.get("cancel_url"),
                client_reference_id=str(order_id) if order_id else None,
                metadata=metadata or None,
            )
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
        return Response(response.json())

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
        return Response(response.json())

    def _paypal_access_token(self):
        if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
            return None, {"error": "Missing PayPal credentials", "hint": "Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET"}

        response, error = self._paypal_request(
            "POST",
            "/v1/oauth2/token",
            auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials"},
        )
        if error:
            return None, error
        return response.json().get("access_token"), None


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


class CategoryFiltersView(generics.GenericAPIView):
    """
    GET /api/categories/{category_slug}/filters/
    Returns all available filters for a category with product counts
    """
    permission_classes = [AllowAny]
    
    @method_decorator(cache_page(60 * 2))
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
            subcategory = SubCategory.objects.filter(slug=sub_slug, category=category).first()
        
        # Get filters linked to this category
        category_filters = CategoryFilter.objects.filter(
            Q(category=category) | Q(subcategory__category=category),
            is_active=True
        )

        # If subcategory is specified, prefer filters tied to it but still include category-level ones
        if subcategory:
            category_filters = category_filters.filter(Q(subcategory=subcategory) | Q(category=category))

        category_filters = category_filters.select_related('filter_type').prefetch_related(
            'filter_type__options'
        ).order_by('display_order')
        
        # Collect unique filter types
        filter_types = []
        seen_ids = set()
        for cf in category_filters:
            ft = cf.filter_type
            if ft.is_active and ft.id not in seen_ids:
                filter_types.append(ft)
                seen_ids.add(ft.id)

        # Precompute product counts in one query (vs per-option loop)
        base_filter_qs = ProductFilterValue.objects.filter(
            product__category=category,
            product__in_stock=True,
            **({"product__subcategory": subcategory} if subcategory else {}),
        )
        option_counts = base_filter_qs.values("filter_option").annotate(product_count=Count("product", distinct=True))
        count_lookup = {row["filter_option"]: row["product_count"] for row in option_counts}
        
        # Attach counts without extra queries
        for ft in filter_types:
            for option in ft.options.filter(is_active=True):
                option.product_count = count_lookup.get(option.id, 0)
        
        serializer = FilterTypeSerializer(filter_types, many=True)
        return Response({'filters': serializer.data})


class ProductStyleLibraryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only list of all style groups across products, for reuse.
    """
    queryset = ProductStyle.objects.select_related("product", "size").all().order_by("product_id", "id")
    serializer_class = ProductStyleLibrarySerializer
    permission_classes = [AllowAny]
