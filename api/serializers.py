from django.contrib.auth.models import User
from django.db import transaction
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
    Promotion,
    FilterType,
    FilterOption,
    CategoryFilter,
    ProductFilterValue,
    DimensionTemplate,
    DimensionRow,
)

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
        return attrs

    class Meta:
        model = SubCategory
        fields = "__all__"


class CategorySerializer(serializers.ModelSerializer):
    subcategories = SubCategorySerializer(many=True, read_only=True)

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
        fields = ("id", "url", "color_name", "alt_text")


class ProductVideoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductVideo
        fields = ("id", "url")


class ProductColorSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductColor
        fields = ("id", "name", "hex_code", "image_url")


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

    class Meta:
        model = MattressOption
        fields = (
            "id",
            "name",
            "description",
            "image_url",
            "price",
            "original_price",
            "enable_bunk_positions",
            "price_top",
            "price_bottom",
            "price_both",
            "categories",
            "subcategories",
            "is_active",
            "sort_order",
            "prices",
        )


class ProductMattressSerializer(serializers.ModelSerializer):
    source_product_name = serializers.CharField(source="source_product.name", read_only=True)
    source_product_slug = serializers.CharField(source="source_product.slug", read_only=True)

    class Meta:
        model = ProductMattress
        fields = (
            "id",
            "name",
            "description",
            "image_url",
            "price",
            "enable_bunk_positions",
            "price_top",
            "price_bottom",
            "price_both",
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


class ProductSerializer(serializers.ModelSerializer):
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
            "dimensions",
            "faqs",
            "delivery_info",
            "returns_guarantee",
            "delivery_title",
            "returns_title",
            "custom_info_sections",
            "delivery_charges",
            "dimension_paragraph",
            "in_stock",
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
            "dimension_images",
            "show_dimensions_table",
            "dimension_template",
            "dimension_template_name",
            "wingback_width_delta_cm",
            "category_name",
            "subcategory_name",
            "category_slug",
            "subcategory_slug",
        )

    def get_mattresses(self, obj):
        """
        Return global mattress options so every bed shows the same admin-defined set.
        """
        options = MattressOption.objects.filter(is_active=True).prefetch_related("prices")
        return MattressOptionSerializer(options, many=True).data

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
class ProductListSerializer(serializers.ModelSerializer):
    images = ProductImageSerializer(many=True, read_only=True)
    sizes = ProductSizeSerializer(many=True, read_only=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    original_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    rating = serializers.DecimalField(max_digits=3, decimal_places=1, read_only=True)
    review_count = serializers.IntegerField(read_only=True)
    category_slug = serializers.ReadOnlyField(source="category.slug")
    subcategory_slug = serializers.ReadOnlyField(source="subcategory.slug")
    filter_values = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "meta_title",
            "meta_description",
            "price",
            "original_price",
            "discount_percentage",
            "in_stock",
            "is_bestseller",
            "is_new",
            "show_size_icons",
            "rating",
            "review_count",
            "images",
            "sizes",
            "dimension_paragraph",
            "show_dimensions_table",
            "sort_order",
            "category_slug",
            "subcategory_slug",
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
            "dimensions",
            "dimension_paragraph",
            "dimension_images",
            "show_dimensions_table",
            "faqs",
            "delivery_info",
            "returns_guarantee",
            "delivery_title",
            "returns_title",
            "custom_info_sections",
            "delivery_charges",
            "in_stock",
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
        new_sort_order = validated_data.get("sort_order", 0)

        with transaction.atomic():
            product = super().create(validated_data)
            self._reorder_products(product, new_sort_order)

            self._sync_filter_values(product, filter_values)
            return product

    def update(self, instance, validated_data):
        # Internal helper used by the view; not a Product model field.
        validated_data.pop("_dimension_template_obj", None)
        filter_values = validated_data.pop("filter_values", None)
        new_sort_order = validated_data.get("sort_order", instance.sort_order)
        with transaction.atomic():
            product = super().update(instance, validated_data)
            self._reorder_products(product, new_sort_order)
            if filter_values is not None:
                self._sync_filter_values(product, filter_values)
            return product

    def _reorder_products(self, product, requested_sort_order):
        try:
            requested_position = int(requested_sort_order)
        except (TypeError, ValueError):
            requested_position = 0

        ordered_products = list(
            Product.objects.select_for_update()
            .filter(sort_order__gt=0)
            .exclude(pk=product.pk)
            .order_by("sort_order", "-created_at", "-id")
        )

        if requested_position > 0:
            insert_index = min(requested_position - 1, len(ordered_products))
            ordered_products.insert(insert_index, product)
            for index, ordered_product in enumerate(ordered_products, start=1):
                if ordered_product.sort_order != index:
                    ordered_product.sort_order = index
                    ordered_product.save(update_fields=["sort_order"])
        elif product.sort_order != 0:
            product.sort_order = 0
            product.save(update_fields=["sort_order"])

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
    products_data = ProductSerializer(source="products", many=True, read_only=True)

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
            "cta_text",
            "cta_link",
            "image",
            "is_active",
            "sort_order",
            "created_at",
            "updated_at",
        )

    def validate(self, attrs):
        category = attrs.get("category") or getattr(self.instance, "category", None)
        subcategory = attrs.get("subcategory") or getattr(self.instance, "subcategory", None)
        incoming_cta = attrs.get("cta_link")
        existing_cta = getattr(self.instance, "cta_link", "") if hasattr(self, "instance") and self.instance else ""

        if (incoming_cta is None or incoming_cta.strip() == ""):
            if subcategory:
                attrs["cta_link"] = existing_cta or f"/category/{subcategory.category.slug}?sub={subcategory.slug}"
            elif category:
                attrs["cta_link"] = existing_cta or f"/category/{category.slug}/subcategories"
            else:
                attrs["cta_link"] = existing_cta
        return attrs


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

    class Meta:
        model = Review
        fields = [
            "id",
            "product",
            "product_name",
            "name",
            "rating",
            "comment",
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
