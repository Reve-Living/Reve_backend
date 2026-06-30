from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q, Case, When, Value, IntegerField
from django.utils.text import slugify
from rest_framework import serializers
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
    HeroSlide,
    LifestyleSection,
    LifestyleArticle,
    Promotion,
    AnnouncementSettings,
    FilterType,
    FilterOption,
    CategoryFilter,
    ProductFilterValue,
    DimensionTemplate,
    DimensionRow,
)


class ProductReviewSummaryMixin:
    def get_rating(self, obj):
        value = getattr(obj, "live_rating", None)
        if value is None:
            value = getattr(obj, "rating", 0)
        return round(float(value or 0), 1)

    def get_review_count(self, obj):
        value = getattr(obj, "live_review_count", None)
        if value is None:
            value = getattr(obj, "review_count", 0)
        return int(value or 0)


def _subcategory_links_to_category(subcategory, category) -> bool:
    if not subcategory or not category:
        return False
    return category.id in subcategory.linked_category_ids()


def _mattress_option_matches_product_scope(item, product) -> bool:
    category_ids = {int(cid) for cid in (item.get("categories") or []) if cid}
    subcategory_ids = {int(sid) for sid in (item.get("subcategories") or []) if sid}
    product_ids = {int(pid) for pid in (item.get("products") or []) if pid}

    if product.subcategory_id:
        matches_taxonomy = (
            product.subcategory_id in subcategory_ids
            or (not subcategory_ids and product.category_id in category_ids)
            or (not subcategory_ids and not category_ids)
        )
    elif product.category_id:
        matches_taxonomy = (
            (product.category_id in category_ids and not subcategory_ids)
            or (not category_ids and not subcategory_ids)
        )
    else:
        matches_taxonomy = not category_ids and not subcategory_ids

    matches_product = not product_ids or product.id in product_ids
    return matches_taxonomy and matches_product


def _clean_text(value, fallback=""):
    text = str(value or "").strip()
    return text or fallback


def _build_unique_slug(model, raw_value, instance=None, fallback="item", extra_filters=None):
    max_length = model._meta.get_field("slug").max_length or 255
    base_slug = (slugify(raw_value) or fallback)[:max_length]
    slug = base_slug
    suffix = 2

    queryset = model.objects.all()
    if extra_filters:
      queryset = queryset.filter(**extra_filters)
    if instance:
      queryset = queryset.exclude(pk=instance.pk)

    while queryset.filter(slug=slug).exists():
      suffix_text = f"-{suffix}"
      truncated = base_slug[: max_length - len(suffix_text)]
      slug = f"{truncated}{suffix_text}"
      suffix += 1

    return slug


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "first_name", "last_name")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("username", "password", "email", "first_name", "last_name")

    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user


class SubCategorySerializer(serializers.ModelSerializer):
    additional_categories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Category.objects.all(), required=False
    )
    linked_category_ids = serializers.SerializerMethodField()
    main_category_name = serializers.CharField(source="category.name", read_only=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        raw_name = attrs.get("name", getattr(instance, "name", ""))
        raw_slug = attrs.get("slug")

        attrs["name"] = _clean_text(raw_name)
        if not attrs["name"]:
            raise serializers.ValidationError({"name": "Name is required"})

        if raw_slug is None and instance and instance.slug:
            attrs["slug"] = instance.slug
        else:
            attrs["slug"] = _build_unique_slug(
                SubCategory,
                raw_slug or attrs["name"],
                instance=instance,
                fallback="subcategory",
            )

        main_category = attrs.get("category", getattr(instance, "category", None))
        additional_categories = attrs.get("additional_categories")
        if additional_categories is None and instance is not None:
            additional_categories = list(instance.additional_categories.all())

        if main_category and additional_categories:
            duplicate_ids = [cat.id for cat in additional_categories if cat.id == main_category.id]
            if duplicate_ids:
                raise serializers.ValidationError(
                    {"additional_categories": "Main category is already selected as the primary category."}
                )
        return attrs

    def get_linked_category_ids(self, obj):
        return obj.linked_category_ids()

    class Meta:
        model = SubCategory
        fields = "__all__"


class CategorySerializer(serializers.ModelSerializer):
    subcategories = serializers.SerializerMethodField()

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        raw_name = attrs.get("name", getattr(instance, "name", ""))
        raw_slug = attrs.get("slug")

        attrs["name"] = _clean_text(raw_name)
        if not attrs["name"]:
            raise serializers.ValidationError({"name": "Name is required"})

        if raw_slug is None and instance and instance.slug:
            attrs["slug"] = instance.slug
        else:
            attrs["slug"] = _build_unique_slug(
                Category,
                raw_slug or attrs["name"],
                instance=instance,
                fallback="category",
            )
        return attrs

    class Meta:
        model = Category
        fields = "__all__"

    def get_subcategories(self, obj):
        request = self.context.get("request")
        include_hidden = bool(
            request
            and (
                getattr(request.user, "is_staff", False)
                or request.headers.get("Authorization")
            )
        )
        primary_subcategories = getattr(obj, "prefetched_primary_subcategories", None)
        shared_subcategories = getattr(obj, "prefetched_shared_subcategories", None)

        if primary_subcategories is None and shared_subcategories is None:
            queryset = (
                SubCategory.objects.filter(Q(category_id=obj.id) | Q(additional_categories__id=obj.id))
                .select_related("category")
                .prefetch_related("additional_categories")
                .distinct()
                .order_by("sort_order", "name")
            )
            if not include_hidden:
                queryset = queryset.filter(is_hidden=False)
            return SubCategorySerializer(queryset, many=True, context=self.context).data

        combined = []
        seen_ids = set()
        for subcategory in [*(primary_subcategories or []), *(shared_subcategories or [])]:
            if subcategory.is_hidden and not include_hidden:
                continue
            if subcategory.id in seen_ids:
                continue
            combined.append(subcategory)
            seen_ids.add(subcategory.id)

        combined.sort(key=lambda subcategory: (int(subcategory.sort_order or 0), subcategory.name.lower()))
        return SubCategorySerializer(combined, many=True, context=self.context).data

    def update(self, instance, validated_data):
        new_sort_order = validated_data.get("sort_order", instance.sort_order)
        old_sort_order = instance.sort_order

        with transaction.atomic():
            if new_sort_order != old_sort_order:
                conflicting_category = (
                    Category.objects.select_for_update()
                    .filter(sort_order=new_sort_order)
                    .exclude(pk=instance.pk)
                    .first()
                )
                if conflicting_category:
                    conflicting_category.sort_order = old_sort_order
                    conflicting_category.save(update_fields=["sort_order"])

            return super().update(instance, validated_data)




class ProductImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductImage
        fields = ("id", "url", "color_name", "style_name", "alt_text", "sort_order")


class ProductVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVideo
        fields = ("id", "url")


class ProductColorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductColor
        fields = ("id", "name", "hex_code", "image_url", "is_available")


class ProductSizeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductSize
        fields = ("id", "name", "description", "price_delta")


class ProductStyleSerializer(serializers.ModelSerializer):
    size = ProductSizeSerializer(read_only=True)
    size_id = serializers.IntegerField(source="size.id", read_only=True)
    size_name = serializers.CharField(source="size.name", read_only=True, default=None)

    class Meta:
        model = ProductStyle
        fields = ("id", "name", "icon_url", "options", "is_shared", "size", "size_id", "size_name")


class ProductStyleLibrarySerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True)
    size = ProductSizeSerializer(read_only=True)
    size_id = serializers.IntegerField(source="size.id", read_only=True)
    size_name = serializers.CharField(source="size.name", read_only=True, default=None)

    class Meta:
        model = ProductStyle
        fields = (
            "id",
            "name",
            "icon_url",
            "options",
            "is_shared",
            "size",
            "size_id",
            "size_name",
            "product_id",
            "product_name",
            "product_slug",
        )


class ProductFabricSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductFabric
        fields = ("id", "name", "image_url", "is_shared", "colors")


class MattressOptionPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MattressOptionPrice
        fields = ("id", "size_label", "price", "original_price", "price_top", "price_bottom", "price_both")


class MattressOptionSerializer(serializers.ModelSerializer):
    prices = MattressOptionPriceSerializer(many=True, required=False)
    categories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Category.objects.all(), required=False, allow_null=True
    )
    subcategories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=SubCategory.objects.all(), required=False, allow_null=True
    )
    products = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Product.objects.all(), required=False, allow_null=True
    )

    class Meta:
        model = MattressOption
        fields = (
            "id",
            "name",
            "display_name",
            "kids_button_label",
            "description",
            "features",
            "image_url",
            "price",
            "original_price",
            "enable_bunk_positions",
            "price_top",
            "price_bottom",
            "price_both",
            "categories",
            "subcategories",
            "products",
            "is_active",
            "sort_order",
            "prices",
        )


class ProductMattressSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(source="product.id", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_category_id = serializers.IntegerField(source="product.category.id", read_only=True)
    product_subcategory_id = serializers.IntegerField(source="product.subcategory.id", read_only=True, allow_null=True)
    source_product_name = serializers.CharField(source="source_product.name", read_only=True)
    source_product_slug = serializers.CharField(source="source_product.slug", read_only=True)

    class Meta:
        model = ProductMattress
        fields = (
            "id",
            "product_id",
            "product_name",
            "product_category_id",
            "product_subcategory_id",
            "name",
            "description",
            "image_url",
            "price",
            "enable_bunk_positions",
            "price_top",
            "price_bottom",
            "price_both",
            "is_hidden",
            "source_product",
            "source_product_name",
            "source_product_slug",
        )


class DimensionRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = DimensionRow
        fields = ("id", "measurement", "values", "display_order")


class DimensionTemplateSerializer(serializers.ModelSerializer):
    rows = DimensionRowSerializer(many=True, read_only=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        raw_name = attrs.get("name", getattr(instance, "name", ""))
        raw_slug = attrs.get("slug")

        attrs["name"] = _clean_text(raw_name)
        if not attrs["name"]:
            raise serializers.ValidationError({"name": "Name is required"})

        if raw_slug is None and instance and instance.slug:
            attrs["slug"] = instance.slug
        else:
            attrs["slug"] = _build_unique_slug(
                DimensionTemplate,
                raw_slug or attrs["name"],
                instance=instance,
                fallback="template",
            )
        return attrs

    class Meta:
        model = DimensionTemplate
        fields = ("id", "name", "slug", "notes", "is_default", "rows")


class ProductSerializer(ProductReviewSummaryMixin, serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    videos = ProductVideoSerializer(many=True, read_only=True)
    colors = ProductColorSerializer(many=True, read_only=True)
    sizes = ProductSizeSerializer(many=True, read_only=True)
    styles = ProductStyleSerializer(many=True, read_only=True)
    fabrics = ProductFabricSerializer(many=True, read_only=True)
    mattresses = serializers.SerializerMethodField()
    filters = serializers.SerializerMethodField()
    computed_dimensions = serializers.SerializerMethodField()
    wingback_width_delta_cm = serializers.SerializerMethodField()
    dimension_template = serializers.SerializerMethodField()
    dimension_template_name = serializers.SerializerMethodField()
    category_name = serializers.ReadOnlyField(source="category.name")
    subcategory_name = serializers.ReadOnlyField(source="subcategory.name")
    category_slug = serializers.ReadOnlyField(source="category.slug")
    subcategory_slug = serializers.ReadOnlyField(source="subcategory.slug")
    stock_status = serializers.CharField(read_only=True)
    imported_from_product = serializers.IntegerField(source="imported_from_product_id", read_only=True, allow_null=True)
    suggested_products = serializers.PrimaryKeyRelatedField(many=True, read_only=True)
    suggested_products_data = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "meta_title",
            "meta_description",
            "category",
            "subcategory",
            "price",
            "original_price",
            "discount_percentage",
            "description",
            "short_description",
            "features",
            "sofa_feature_highlights",
            "dimensions",
            "faqs",
            "delivery_info",
            "returns_guarantee",
            "delivery_title",
            "returns_title",
            "custom_info_sections",
            "delivery_charges",
            "assembly_service_enabled",
            "assembly_service_price",
            "stock_status",
            "dimension_paragraph",
            "dimension_note",
            "in_stock",
            "is_hidden",
            "is_bestseller",
            "is_new",
            "show_size_icons",
            "sort_order",
            "rating",
            "review_count",
            "created_at",
            "updated_at",
            "images",
            "videos",
            "colors",
            "sizes",
            "styles",
            "fabrics",
            "mattresses",
            "filters",
            "computed_dimensions",
            "dimension_paragraph",
            "dimension_note",
            "dimension_images",
            "show_dimensions_table",
            "dimension_template",
            "dimension_template_name",
            "wingback_width_delta_cm",
            "category_name",
            "subcategory_name",
            "category_slug",
            "subcategory_slug",
            "imported_from_product",
            "suggested_products",
            "suggested_products_data",
        )

    def get_suggested_products_data(self, obj):
        queryset = getattr(obj, "prefetched_suggested_products", None)
        if queryset is None:
            queryset = (
                obj.suggested_products.filter(is_hidden=False)
                .select_related("category", "subcategory")
                .prefetch_related("images", "sizes")
                .order_by("sort_order", "-created_at")
            )
        return ProductListSerializer(queryset, many=True).data

    def get_mattresses(self, obj):
        """
        Return mattresses assigned via the mattress manager for the product's
        category/subcategory, then apply any product-specific overrides by name.
        """
        request = self.context.get("request")
        is_admin_request = bool(request and request.user and request.user.is_authenticated and request.user.is_staff)
        options = (
            MattressOption.objects.filter(is_active=True)
            .prefetch_related("prices", "categories", "subcategories", "products")
            .annotate(
                sort_priority=Case(
                    When(sort_order__gt=0, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            )
            .order_by("sort_priority", "sort_order", "name")
        )

        cache_key = (
            f"mattress-options:product-detail:v2:{obj.id}:{obj.category_id or 'none'}:{obj.subcategory_id or 'none'}"
        )
        base_items = cache.get(cache_key)
        if base_items is None:
            serialized_items = list(MattressOptionSerializer(options, many=True).data)
            base_items = [
                item
                for item in serialized_items
                if _mattress_option_matches_product_scope(item, obj)
            ]
            cache.set(cache_key, base_items, 60 * 5)
        overrides = getattr(obj, "mattresses", None)
        override_items = list(overrides.all()) if overrides is not None else []
        override_lookup = {
            str(item.name or "").strip().lower(): item
            for item in override_items
            if str(item.name or "").strip()
        }

        merged = []
        for item in base_items:
            key = str(item.get("name", "")).strip().lower()
            override = override_lookup.get(key)
            if override and override.is_hidden:
                if is_admin_request:
                    merged.append(
                        {
                            **item,
                            "name": override.name or item.get("name"),
                            "description": override.description or item.get("description"),
                            "features": item.get("features"),
                            "image_url": override.image_url or item.get("image_url"),
                            "price": override.price if override.price is not None else item.get("price"),
                            "enable_bunk_positions": override.enable_bunk_positions,
                            "price_top": override.price_top if override.price_top is not None else item.get("price_top"),
                            "price_bottom": override.price_bottom if override.price_bottom is not None else item.get("price_bottom"),
                            "price_both": override.price_both if override.price_both is not None else item.get("price_both"),
                            "is_hidden": True,
                        }
                    )
                continue
            if not override:
                merged.append({**item, "is_hidden": False})
                continue

            merged.append(
                {
                    **item,
                    "name": override.name or item.get("name"),
                    "description": override.description or item.get("description"),
                    "features": item.get("features"),
                    "image_url": override.image_url or item.get("image_url"),
                    "price": override.price if override.price is not None else item.get("price"),
                    "enable_bunk_positions": override.enable_bunk_positions,
                    "price_top": override.price_top if override.price_top is not None else item.get("price_top"),
                    "price_bottom": override.price_bottom if override.price_bottom is not None else item.get("price_bottom"),
                    "price_both": override.price_both if override.price_both is not None else item.get("price_both"),
                    "is_hidden": False,
                }
            )

        return merged

    def get_filters(self, obj):
        # use prefetched data when available to avoid N+1
        values = getattr(obj, "filter_values_all", None)
        if values is None:
            values = ProductFilterValue.objects.filter(product=obj).select_related("filter_option__filter_type")
        else:
            values = list(values)
        by_type = {}
        for val in values:
            ft = val.filter_option.filter_type
            if ft.id not in by_type:
                by_type[ft.id] = {
                    "id": ft.id,
                    "name": ft.name,
                    "slug": ft.slug,
                    "display_type": ft.display_type,
                    "icon_url": ft.icon_url,
                    "display_hint": ft.display_hint,
                    "is_default": ft.is_default,
                    "is_expanded_by_default": ft.is_expanded_by_default,
                    "options": [],
                }
            opt = val.filter_option
            by_type[ft.id]["options"].append({
                "id": opt.id,
                "name": opt.name,
                "slug": opt.slug,
                "color_code": opt.color_code,
                "icon_url": opt.icon_url,
                "price_delta": opt.price_delta,
                "is_wingback": opt.is_wingback,
                "metadata": opt.metadata,
            })
        # preserve display order: defaults first, then name
        ordered = sorted(by_type.values(), key=lambda item: (0 if item["is_default"] else 1, item["name"]))
        return ordered

    def get_dimension_template(self, obj):
        if hasattr(obj, "dimension_template_link"):
            return obj.dimension_template_link.template.id
        return None

    def get_dimension_template_name(self, obj):
        if hasattr(obj, "dimension_template_link"):
            return obj.dimension_template_link.template.name
        return ""

    def _merge_dimensions(self, obj):
        template_rows = []
        if hasattr(obj, "dimension_template_link"):
            template_rows = list(obj.dimension_template_link.template.rows.all().order_by("display_order"))
        override_rows = obj.dimensions or []
        merged = []
        # map for quick override lookup
        override_map = {row.get("measurement"): row.get("values", {}) for row in override_rows if isinstance(row, dict)}
        for row in template_rows:
            values = dict(row.values or {})
            if row.measurement in override_map:
                values.update({k: v for k, v in override_map[row.measurement].items() if v})
            merged.append({"measurement": row.measurement, "values": values})
        # Add overrides that weren't in template
        for measurement, values in override_map.items():
            if not any(r["measurement"] == measurement for r in merged):
                merged.append({"measurement": measurement, "values": values})
        return merged

    def get_computed_dimensions(self, obj):
        return self._merge_dimensions(obj)

    def get_wingback_width_delta_cm(self, obj):
        return 4  # requirement: wingback headboard adds approx 4 cm width


# Lighter serializer for list views to keep responses smaller
class ProductListSerializer(ProductReviewSummaryMixin, serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    sizes = ProductSizeSerializer(many=True, read_only=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    original_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    rating = serializers.SerializerMethodField()
    review_count = serializers.SerializerMethodField()
    category_name = serializers.ReadOnlyField(source="category.name")
    subcategory_name = serializers.ReadOnlyField(source="subcategory.name")
    category_slug = serializers.ReadOnlyField(source="category.slug")
    subcategory_slug = serializers.ReadOnlyField(source="subcategory.slug")
    stock_status = serializers.CharField(read_only=True)
    imported_from_product = serializers.IntegerField(source="imported_from_product_id", read_only=True, allow_null=True)
    filter_values = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "meta_title",
            "meta_description",
            "category",
            "subcategory",
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
            "images",
            "sizes",
            "dimension_paragraph",
            "dimension_note",
            "show_dimensions_table",
            "sort_order",
            "assembly_service_enabled",
            "assembly_service_price",
            "short_description",
            "category_name",
            "subcategory_name",
            "category_slug",
            "subcategory_slug",
            "imported_from_product",
            "filter_values",
        ]

    def get_filter_values(self, obj):
        # Lightweight payload for client-side filtering
        values = getattr(obj, "filter_values_all", None)
        if values is None:
            values = ProductFilterValue.objects.filter(product=obj).select_related("filter_option__filter_type")
        result = []
        for val in values:
            ft = val.filter_option.filter_type
            result.append(
                {
                    "filter_type": ft.slug,
                    "option": val.filter_option.slug,
                    "filter_option_id": val.filter_option.id,
                }
            )
        return result


class ProductSummarySerializer(serializers.ModelSerializer):
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    category_name = serializers.ReadOnlyField(source="category.name")
    subcategory_name = serializers.ReadOnlyField(source="subcategory.name")
    category_slug = serializers.ReadOnlyField(source="category.slug")
    subcategory_slug = serializers.ReadOnlyField(source="subcategory.slug")
    stock_status = serializers.CharField(read_only=True)
    imported_from_product = serializers.IntegerField(source="imported_from_product_id", read_only=True, allow_null=True)

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "category",
            "subcategory",
            "price",
            "stock_status",
            "in_stock",
            "is_hidden",
            "is_bestseller",
            "is_new",
            "sort_order",
            "category_name",
            "subcategory_name",
            "category_slug",
            "subcategory_slug",
            "imported_from_product",
        ]


class ProductWriteSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=Product._meta.get_field("slug").max_length or 255,
    )
    images = ProductImageSerializer(many=True, required=False)
    videos = ProductVideoSerializer(many=True, required=False)
    colors = ProductColorSerializer(many=True, required=False)
    sizes = ProductSizeSerializer(many=True, required=False)
    styles = ProductStyleSerializer(many=True, required=False)
    fabrics = ProductFabricSerializer(many=True, required=False)
    mattresses = ProductMattressSerializer(many=True, required=False)
    dimension_template = serializers.IntegerField(required=False, allow_null=True, write_only=True)
    filter_values = serializers.ListField(child=serializers.DictField(), required=False)
    suggested_products = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Product.objects.all(),
        required=False,
    )

    class Meta:
        model = Product
        fields = (
            "id",
            "name",
            "slug",
            "meta_title",
            "meta_description",
            "category",
            "subcategory",
            "price",
            "original_price",
            "discount_percentage",
            "description",
            "short_description",
            "features",
            "sofa_feature_highlights",
            "dimensions",
            "dimension_paragraph",
            "dimension_note",
            "dimension_images",
            "show_dimensions_table",
            "faqs",
            "delivery_info",
            "returns_guarantee",
            "delivery_title",
            "returns_title",
            "custom_info_sections",
            "delivery_charges",
            "assembly_service_enabled",
            "assembly_service_price",
            "stock_status",
            "in_stock",
            "is_hidden",
            "is_bestseller",
            "is_new",
            "show_size_icons",
            "sort_order",
            "rating",
            "review_count",
            "images",
            "videos",
            "colors",
            "sizes",
            "styles",
            "fabrics",
            "mattresses",
            "dimension_template",
            "filter_values",
            "suggested_products",
            "sort_order",
        )

    def _generate_unique_slug(self, raw_value: str) -> str:
        max_length = Product._meta.get_field("slug").max_length or 50
        base_slug = (slugify(raw_value) or "product")[:max_length]
        slug = base_slug
        counter = 1

        queryset = Product.objects.all()
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)

        while queryset.filter(slug=slug).exists():
            suffix = f"-{counter}"
            truncated_base = base_slug[: max_length - len(suffix)]
            slug = f"{truncated_base}{suffix}"
            counter += 1

        return slug

    def validate(self, attrs):
        description = attrs.get("description", getattr(self.instance, "description", ""))
        short_description = attrs.get("short_description", getattr(self.instance, "short_description", ""))
        category = attrs.get("category", getattr(self.instance, "category", None))
        subcategory = (
            attrs.get("subcategory")
            if "subcategory" in attrs
            else getattr(self.instance, "subcategory", None)
        )

        if isinstance(description, str):
            description = description.strip()
            attrs["description"] = description

        if isinstance(short_description, str):
            short_description = short_description.strip()

        if not short_description and isinstance(description, str) and description:
            first_sentence = description.split(".")[0].strip()
            short_description = first_sentence or description
            if len(short_description) > 220:
                short_description = f"{short_description[:217].rstrip()}..."

        attrs["short_description"] = short_description or ""

        if subcategory is not None and category is not None and not subcategory.is_linked_to_category(category):
            raise serializers.ValidationError(
                {"subcategory": f"{subcategory.name} is not linked to {category.name}."}
            )

        raw_sofa_feature_highlights = attrs.get(
            "sofa_feature_highlights",
            getattr(self.instance, "sofa_feature_highlights", []),
        )
        cleaned_sofa_feature_highlights = []
        if isinstance(raw_sofa_feature_highlights, list):
            for item in raw_sofa_feature_highlights:
                label = str(item or "").strip()
                if label:
                    cleaned_sofa_feature_highlights.append(label)
        attrs["sofa_feature_highlights"] = cleaned_sofa_feature_highlights

        raw_dimensions = attrs.get("dimensions", getattr(self.instance, "dimensions", []))
        cleaned_dimensions = []
        if isinstance(raw_dimensions, list):
            for row in raw_dimensions:
                if not isinstance(row, dict):
                    continue
                measurement = str(row.get("measurement", "")).strip()
                values = row.get("values", {})
                if not measurement or not isinstance(values, dict):
                    continue
                cleaned_values = {}
                for key, value in values.items():
                    size_key = str(key).strip()
                    if not size_key:
                        continue
                    cleaned_values[size_key] = str(value).strip()
                if cleaned_values:
                    cleaned_dimensions.append({"measurement": measurement, "values": cleaned_values})
        attrs["dimensions"] = cleaned_dimensions
        if "dimension_paragraph" in attrs:
            dp = attrs.get("dimension_paragraph") or ""
            attrs["dimension_paragraph"] = str(dp).strip()
        if "dimension_note" in attrs:
            dn = attrs.get("dimension_note") or ""
            attrs["dimension_note"] = str(dn).strip()
        if "dimension_images" in attrs:
            imgs = attrs.get("dimension_images") or []
            cleaned = []
            if isinstance(imgs, list):
                for entry in imgs:
                    if not isinstance(entry, dict):
                        continue
                    size = str(entry.get("size", "")).strip()
                    url = str(entry.get("url", "")).strip()
                    if size and url:
                        cleaned.append({"size": size, "url": url})
            attrs["dimension_images"] = cleaned

        fallback_in_stock = attrs.get(
            "in_stock",
            getattr(self.instance, "in_stock", True) if self.instance is not None else True,
        )
        attrs["stock_status"] = Product.normalize_stock_status(
            attrs.get("stock_status"),
            fallback_in_stock=bool(fallback_in_stock),
        )
        attrs["in_stock"] = Product.is_stock_status_purchasable(attrs["stock_status"])

        raw_slug_or_name = attrs.get("slug") or attrs.get("name")
        if raw_slug_or_name:
            attrs["slug"] = self._generate_unique_slug(raw_slug_or_name)
        elif self.instance and self.instance.slug:
            attrs["slug"] = self._generate_unique_slug(self.instance.slug)

        dt_id = attrs.pop("dimension_template", None)
        if dt_id:
            try:
                attrs["_dimension_template_obj"] = DimensionTemplate.objects.get(id=dt_id)
            except DimensionTemplate.DoesNotExist:
                raise serializers.ValidationError({"dimension_template": "Dimension template not found"})
        elif dt_id is None:
            attrs["_dimension_template_obj"] = None
        return attrs

    def create(self, validated_data):
        # Internal helper used by the view; not a Product model field.
        validated_data.pop("_dimension_template_obj", None)
        filter_values = validated_data.pop("filter_values", [])
        suggested_products = validated_data.pop("suggested_products", [])
        new_sort_order = validated_data.get("sort_order", 0)

        with transaction.atomic():
            product = super().create(validated_data)
            self._reorder_products(product, new_sort_order, previous_sort_order=0)

            self._sync_filter_values(product, filter_values)
            product.suggested_products.set([item for item in suggested_products if item.pk != product.pk])
            return product

    def update(self, instance, validated_data):
        # Internal helper used by the view; not a Product model field.
        validated_data.pop("_dimension_template_obj", None)
        filter_values = validated_data.pop("filter_values", None)
        suggested_products = validated_data.pop("suggested_products", None)
        previous_sort_order = instance.sort_order
        previous_category_id = instance.category_id
        previous_subcategory_id = instance.subcategory_id
        new_sort_order = validated_data.get("sort_order", instance.sort_order)
        with transaction.atomic():
            product = super().update(instance, validated_data)
            scope_changed = (
                product.category_id != previous_category_id
                or product.subcategory_id != previous_subcategory_id
            )
            if scope_changed or new_sort_order != previous_sort_order:
                self._reorder_products(product, new_sort_order, previous_sort_order=previous_sort_order)
            if filter_values is not None:
                self._sync_filter_values(product, filter_values)
            if suggested_products is not None:
                product.suggested_products.set([item for item in suggested_products if item.pk != product.pk])
            return product

    def _reorder_products(self, product, requested_sort_order, previous_sort_order=0):
        try:
            requested_position = max(int(requested_sort_order), 0)
        except (TypeError, ValueError):
            requested_position = 0

        if requested_position <= 0:
            if product.sort_order != 0:
                product.sort_order = 0
                product.save(update_fields=["sort_order"])
            return

        scoped_products = Product.objects.select_for_update().exclude(pk=product.pk).filter(category=product.category)
        if product.subcategory_id:
            scoped_products = scoped_products.filter(subcategory_id=product.subcategory_id)

        ordered_products = list(scoped_products.order_by("sort_order", "-created_at", "-id"))
        insert_at = min(max(requested_position, 1), len(ordered_products) + 1) - 1
        ordered_products.insert(insert_at, product)

        dirty_products = []
        for new_position, ordered_product in enumerate(ordered_products, start=1):
            if ordered_product.sort_order != new_position:
                ordered_product.sort_order = new_position
                dirty_products.append(ordered_product)

        if dirty_products:
            Product.objects.bulk_update(dirty_products, ["sort_order"])

    def _sync_filter_values(self, product, filter_values):
        if filter_values is None:
            return
        ids = []
        for item in filter_values or []:
            option_id = item.get("filter_option") or item.get("filter_option_id")
            if option_id:
                try:
                    ids.append(int(option_id))
                except (TypeError, ValueError):
                    continue
        ProductFilterValue.objects.filter(product=product).exclude(filter_option_id__in=ids).delete()
        existing = set(
            ProductFilterValue.objects.filter(product=product, filter_option_id__in=ids).values_list(
                "filter_option_id", flat=True
            )
        )
        to_create = [pid for pid in ids if pid not in existing]
        ProductFilterValue.objects.bulk_create(
            [ProductFilterValue(product=product, filter_option_id=pid) for pid in to_create]
        )


class CollectionSerializer(serializers.ModelSerializer):
    slug = serializers.CharField(required=False, allow_blank=True)
    products = serializers.PrimaryKeyRelatedField(many=True, queryset=Product.objects.all(), required=False)
    products_data = ProductListSerializer(source="products", many=True, read_only=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        raw_slug = attrs.get("slug")
        name_for_slug = attrs.get("name") or (instance.name if instance else None)
        attrs["name"] = _clean_text(attrs.get("name", getattr(instance, "name", "")))

        if not attrs["name"]:
            raise serializers.ValidationError({"name": "Name is required"})

        if raw_slug is None and instance and instance.slug:
            attrs["slug"] = instance.slug
        else:
            attrs["slug"] = _build_unique_slug(
                Collection,
                raw_slug or name_for_slug or attrs["name"],
                instance=instance,
                fallback="collection",
            )
        return attrs

    class Meta:
        model = Collection
        fields = (
            "id",
            "name",
            "slug",
            "description",
            "image",
            "is_featured",
            "sort_order",
            "created_at",
            "updated_at",
            "products",
            "products_data",
        )


class HeroSlideSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    category_slug = serializers.CharField(source="category.slug", read_only=True)
    subcategory_name = serializers.CharField(source="subcategory.name", read_only=True)
    subcategory_slug = serializers.CharField(source="subcategory.slug", read_only=True)
    selected_subcategories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=SubCategory.objects.all(), required=False
    )
    selected_subcategory_slugs = serializers.SerializerMethodField()
    selected_subcategory_names = serializers.SerializerMethodField()

    class Meta:
        model = HeroSlide
        fields = (
            "id",
            "title",
            "subtitle",
            "category",
            "category_name",
            "category_slug",
            "subcategory",
            "subcategory_name",
            "subcategory_slug",
            "selected_subcategories",
            "selected_subcategory_slugs",
            "selected_subcategory_names",
            "cta_text",
            "cta_link",
            "image",
            "is_active",
            "sort_order",
            "created_at",
            "updated_at",
        )

    def get_selected_subcategory_slugs(self, obj):
        return [sub.slug for sub in obj.selected_subcategories.all()]

    def get_selected_subcategory_names(self, obj):
        return [sub.name for sub in obj.selected_subcategories.all()]

    def validate(self, attrs):
        category = attrs.get("category") or getattr(self.instance, "category", None)
        subcategory = attrs.get("subcategory") or getattr(self.instance, "subcategory", None)
        incoming_cta = attrs.get("cta_link")
        existing_cta = getattr(self.instance, "cta_link", "") if hasattr(self, "instance") and self.instance else ""
        selected_subcategories = attrs.get("selected_subcategories")

        if selected_subcategories is not None and not category and selected_subcategories:
            raise serializers.ValidationError(
                {"selected_subcategories": "Choose a category before selecting subcategories for a slide."}
            )

        if selected_subcategories is not None and category:
            invalid = [sub.name for sub in selected_subcategories if not _subcategory_links_to_category(sub, category)]
            if invalid:
                raise serializers.ValidationError(
                    {"selected_subcategories": f"These subcategories do not belong to {category.name}: {', '.join(invalid)}"}
                )

        if (incoming_cta is None or incoming_cta.strip() == ""):
            if subcategory:
                target_category = category if _subcategory_links_to_category(subcategory, category) else subcategory.category
                attrs["cta_link"] = existing_cta or f"/category/{target_category.slug}?sub={subcategory.slug}"
            elif category:
                attrs["cta_link"] = existing_cta or f"/category/{category.slug}/subcategories"
            else:
                attrs["cta_link"] = existing_cta
        return attrs


class LifestyleArticleSerializer(serializers.ModelSerializer):
    read_more_target = serializers.SerializerMethodField()
    related_articles = serializers.SerializerMethodField()

    class Meta:
        model = LifestyleArticle
        fields = (
            "id",
            "section",
            "title",
            "slug",
            "description",
            "card_image",
            "image",
            "article_title",
            "article_intro",
            "article_body",
            "article_content",
            "article_sections",
            "read_more_type",
            "read_more_url",
            "read_more_pdf",
            "read_more_target",
            "related_articles",
            "is_active",
            "sort_order",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        title = str(attrs.get("title", getattr(instance, "title", "")) or "").strip()
        read_more_type = attrs.get("read_more_type", getattr(instance, "read_more_type", LifestyleArticle.READ_MORE_NONE))
        read_more_url = str(attrs.get("read_more_url", getattr(instance, "read_more_url", "")) or "").strip()
        read_more_pdf = str(attrs.get("read_more_pdf", getattr(instance, "read_more_pdf", "")) or "").strip()
        article_title = str(attrs.get("article_title", getattr(instance, "article_title", "")) or "").strip()
        article_intro = str(attrs.get("article_intro", getattr(instance, "article_intro", "")) or "").strip()
        article_body = str(attrs.get("article_body", getattr(instance, "article_body", "")) or "").strip()
        article_content = attrs.get("article_content", getattr(instance, "article_content", []))
        article_sections = attrs.get("article_sections", getattr(instance, "article_sections", []))

        if not title:
            raise serializers.ValidationError({"title": "Article title is required"})

        if read_more_type == LifestyleArticle.READ_MORE_URL and not read_more_url:
            raise serializers.ValidationError({"read_more_url": "Add a URL for the Read More button"})
        if read_more_type == LifestyleArticle.READ_MORE_PDF and not read_more_pdf:
            raise serializers.ValidationError({"read_more_pdf": "Add a PDF link for the Read More button"})
        if read_more_type == LifestyleArticle.READ_MORE_ARTICLE and not article_body:
            has_blocks = isinstance(article_content, list) and len(article_content) > 0
            has_sections = isinstance(article_sections, list) and len(article_sections) > 0
            if not has_blocks and not has_sections:
                raise serializers.ValidationError({"article_body": "Add article content for the article page"})

        attrs["read_more_url"] = read_more_url
        attrs["read_more_pdf"] = read_more_pdf
        attrs["title"] = title
        attrs["article_title"] = article_title
        attrs["article_intro"] = article_intro
        attrs["article_body"] = article_body
        attrs["article_content"] = article_content if isinstance(article_content, list) else []
        attrs["article_sections"] = article_sections if isinstance(article_sections, list) else []
        attrs["description"] = str(attrs.get("description", getattr(instance, "description", "")) or "").strip()
        attrs["card_image"] = str(attrs.get("card_image", getattr(instance, "card_image", "")) or "").strip()
        attrs["image"] = str(attrs.get("image", getattr(instance, "image", "")) or "").strip()
        return attrs

    def get_read_more_target(self, obj):
        if obj.read_more_type == LifestyleArticle.READ_MORE_PDF:
            return obj.read_more_pdf
        if obj.read_more_type == LifestyleArticle.READ_MORE_URL:
            return obj.read_more_url
        if obj.read_more_type == LifestyleArticle.READ_MORE_ARTICLE:
            return f"/transform-your-home/{obj.slug}"
        return ""

    def get_related_articles(self, obj):
        queryset = (
            LifestyleArticle.objects.filter(section=obj.section, is_active=True, section__is_active=True)
            .exclude(pk=obj.pk)
            .order_by("sort_order", "-updated_at", "-id")[:3]
        )
        return [
            {
                "id": article.id,
                "title": article.title,
                "slug": article.slug,
                "description": article.description,
                "image": article.image,
                "read_more_target": f"/transform-your-home/{article.slug}",
            }
            for article in queryset
        ]


class LifestyleSectionSerializer(serializers.ModelSerializer):
    articles = serializers.SerializerMethodField()

    class Meta:
        model = LifestyleSection
        fields = (
            "id",
            "title",
            "subtitle",
            "is_active",
            "created_at",
            "updated_at",
            "articles",
        )

    def get_articles(self, obj):
        request = self.context.get("request")
        queryset = obj.articles.all()
        if request and not getattr(request.user, "is_staff", False):
            queryset = queryset.filter(is_active=True)
        elif request and request.query_params.get("active_only") in ("1", "true", "True"):
            queryset = queryset.filter(is_active=True)
        return LifestyleArticleSerializer(queryset, many=True, context=self.context).data


class PromotionSerializer(serializers.ModelSerializer):
    categories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Category.objects.all(), required=False, allow_null=True
    )
    subcategories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=SubCategory.objects.all(), required=False, allow_null=True
    )
    category_names = serializers.SerializerMethodField()
    subcategory_names = serializers.SerializerMethodField()
    is_currently_live = serializers.SerializerMethodField()

    class Meta:
        model = Promotion
        fields = (
            "id",
            "name",
            "code",
            "announcement_text",
            "discount_percentage",
            "start_date",
            "end_date",
            "categories",
            "subcategories",
            "category_names",
            "subcategory_names",
            "is_active",
            "is_currently_live",
            "sort_order",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        instance = getattr(self, "instance", None)
        start_date = attrs.get("start_date", getattr(instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(instance, "end_date", None))
        code = str(attrs.get("code", getattr(instance, "code", "")) or "").strip().upper()

        if not str(attrs.get("name", getattr(instance, "name", "")) or "").strip():
            raise serializers.ValidationError({"name": "Name is required"})
        if not code:
            raise serializers.ValidationError({"code": "Promo code is required"})
        if start_date and end_date and start_date > end_date:
            raise serializers.ValidationError({"end_date": "End date must be on or after the start date"})

        attrs["name"] = str(attrs.get("name", getattr(instance, "name", "")) or "").strip()
        attrs["code"] = code
        attrs["announcement_text"] = str(
            attrs.get("announcement_text", getattr(instance, "announcement_text", "")) or ""
        ).strip()
        return attrs

    def get_category_names(self, obj):
        return [category.name for category in obj.categories.all()]

    def get_subcategory_names(self, obj):
        return [subcategory.name for subcategory in obj.subcategories.all()]

    def get_is_currently_live(self, obj):
        from django.utils import timezone

        today = timezone.localdate()
        return bool(obj.is_active and obj.start_date <= today <= obj.end_date)


class AnnouncementSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AnnouncementSettings
        fields = ("id", "default_text", "updated_at")

    def validate_default_text(self, value):
        return str(value or "").strip()


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source="product.name")

    class Meta:
        model = OrderItem
        fields = "__all__"


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = "__all__"


class ReviewSerializer(serializers.ModelSerializer):
    product_name = serializers.ReadOnlyField(source="product.name")
    created_by_username = serializers.SerializerMethodField()

    def get_created_by_username(self, obj):
        return obj.created_by.username if obj.created_by else None

    def validate_media_url(self, url):
        parsed = urlparse(url)
        path = parsed.path or ""
        if "/review-media/" not in path:
            raise serializers.ValidationError("Please upload media before submitting the review")

        if not parsed.netloc:
            return

        allowed_hosts = {
            urlparse(getattr(settings, "MEDIA_URL", "")).netloc,
            getattr(settings, "AWS_S3_CUSTOM_DOMAIN", ""),
        }
        request = self.context.get("request")
        if request:
            allowed_hosts.add(request.get_host())

        if parsed.netloc not in {host for host in allowed_hosts if host}:
            raise serializers.ValidationError("Please upload media before submitting the review")

    def validate_media(self, value):
        if value in (None, ""):
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError("Review media must be a list")
        if len(value) > 6:
            raise serializers.ValidationError("Please upload no more than 6 images or videos")

        normalized = []
        for item in value:
            if isinstance(item, str):
                url = item.strip()
                media_type = "image"
                name = ""
                mime_type = ""
            elif isinstance(item, dict):
                url = str(item.get("url") or "").strip()
                media_type = str(item.get("type") or item.get("media_type") or "").strip().lower()
                name = str(item.get("name") or "").strip()[:255]
                mime_type = str(item.get("mime_type") or item.get("mime") or "").strip()[:120]
            else:
                raise serializers.ValidationError("Each media item must be an uploaded media object")

            if not url:
                raise serializers.ValidationError("Each media item needs a URL")
            if url.startswith("data:"):
                raise serializers.ValidationError("Please upload media before submitting the review")
            self.validate_media_url(url)
            if media_type not in ("image", "video"):
                raise serializers.ValidationError("Review media must be an image or video")

            normalized.append(
                {
                    "url": url,
                    "type": media_type,
                    "name": name,
                    "mime_type": mime_type,
                }
            )

        return normalized

    class Meta:
        model = Review
        fields = [
            "id",
            "product",
            "product_name",
            "name",
            "rating",
            "comment",
            "media",
            "is_visible",
            "created_at",
            "created_by",
            "created_by_username",
        ]
        read_only_fields = ("created_at", "created_by")


class FilterOptionSerializer(serializers.ModelSerializer):
    product_count = serializers.SerializerMethodField()
    filter_type = serializers.PrimaryKeyRelatedField(queryset=FilterType.objects.all(), write_only=True)
    filter_type_id = serializers.IntegerField(source="filter_type.id", read_only=True)
    filter_type_name = serializers.CharField(source="filter_type.name", read_only=True)
    
    class Meta:
        model = FilterOption
        fields = [
            'id',
            'name',
            'slug',
            'filter_type',
            'filter_type_id',
            'filter_type_name',
            'color_code',
            'icon_url',
            'price_delta',
            'is_wingback',
            'metadata',
            'product_count',
        ]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        raw_name = attrs.get("name", getattr(self.instance, "name", ""))
        raw_slug = attrs.get("slug", getattr(self.instance, "slug", ""))
        filter_type = attrs.get("filter_type") or getattr(self.instance, "filter_type", None)

        cleaned_name = _clean_text(raw_name)
        cleaned_slug = _build_unique_slug(
            FilterOption,
            raw_slug or cleaned_name,
            instance=getattr(self, "instance", None),
            fallback="option",
            extra_filters={"filter_type": filter_type} if filter_type else None,
        )

        if not cleaned_name:
            raise serializers.ValidationError({"name": "Name is required"})

        attrs["name"] = cleaned_name
        attrs["slug"] = cleaned_slug
        return attrs
    
    def get_product_count(self, obj):
        # This will be computed based on current category context
        return getattr(obj, 'product_count', 0)


class FilterTypeSerializer(serializers.ModelSerializer):
    options = FilterOptionSerializer(many=True, read_only=True)
    
    class Meta:
        model = FilterType
        fields = ['id', 'name', 'slug', 'display_type', 'icon_url', 'display_hint', 'is_default', 'is_expanded_by_default', 'options']

    def validate(self, attrs):
        attrs = super().validate(attrs)
        raw_name = attrs.get("name", getattr(self.instance, "name", ""))
        raw_slug = attrs.get("slug", getattr(self.instance, "slug", ""))

        cleaned_name = _clean_text(raw_name)
        cleaned_slug = _build_unique_slug(
            FilterType,
            raw_slug or cleaned_name,
            instance=getattr(self, "instance", None),
            fallback="filter",
        )

        if not cleaned_name:
            raise serializers.ValidationError({"name": "Name is required"})

        attrs["name"] = cleaned_name
        attrs["slug"] = cleaned_slug
        return attrs


class CategoryFilterSerializer(serializers.ModelSerializer):
    filter_type_name = serializers.ReadOnlyField(source="filter_type.name")
    category_name = serializers.ReadOnlyField(source="category.name")
    subcategory_name = serializers.ReadOnlyField(source="subcategory.name")
    
    class Meta:
        model = CategoryFilter
        fields = (
            "id",
            "category",
            "subcategory",
            "filter_type",
            "display_order",
            "is_active",
            "category_name",
            "subcategory_name",
            "filter_type_name",
        )


class ProductFilterValueSerializer(serializers.ModelSerializer):
    filter_option_name = serializers.ReadOnlyField(source="filter_option.name")
    filter_type_name = serializers.ReadOnlyField(source="filter_option.filter_type.name")
    
    class Meta:
        model = ProductFilterValue
        fields = "__all__"
