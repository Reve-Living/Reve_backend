from django.contrib import admin
from django.utils.html import format_html, format_html_join
from .models import (
    Category,
    SubCategory,
    Product,
    ProductImage,
    ProductVideo,
    ProductColor,
    ProductSize,
    ProductStyle,
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
    ProductDimensionTemplate,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name", "slug"]


@admin.register(SubCategory)
class SubCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "slug", "sort_order")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name", "slug"]


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


class ProductVideoInline(admin.TabularInline):
    model = ProductVideo
    extra = 0


class ProductColorInline(admin.TabularInline):
    model = ProductColor
    extra = 1
    fields = ['name', 'hex_code', 'is_available', 'stock_status']


class ProductSizeInline(admin.TabularInline):
    model = ProductSize
    extra = 0
    fields = ["name", "description", "stock_status"]


class ProductStyleInline(admin.TabularInline):
    model = ProductStyle
    extra = 0
    fields = ["name", "icon_url", "options"]


class ProductFilterValueInline(admin.TabularInline):
    model = ProductFilterValue
    extra = 1
    autocomplete_fields = ['filter_option']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "stock_status", "in_stock", "is_hidden", "is_bestseller", "is_new", "sort_order")
    list_filter = ("category", "stock_status", "in_stock", "is_hidden", "is_bestseller", "is_new")
    ordering = ("sort_order", "-created_at")
    search_fields = ["name", "slug", "short_description", "description"]
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("suggested_products",)
    inlines = [ProductImageInline, ProductVideoInline, ProductColorInline, ProductSizeInline, ProductStyleInline, ProductFilterValueInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("id", "email", "total_amount", "promo_code", "status", "payment_method", "created_at")
    list_filter = ("status", "payment_method")
    inlines = [OrderItemInline]
    readonly_fields = ("special_notes", "reference_images_preview", "created_at")
    fieldsets = (
        (None, {
            "fields": (
                "user",
                ("first_name", "last_name"),
                ("email", "phone"),
                "address",
                ("city", "postal_code"),
            )
        }),
        ("Payment & Status", {
            "fields": (
                ("total_amount", "delivery_charges", "promo_discount_amount"),
                ("promo_code", "promo_name"),
                ("status", "payment_method", "payment_id"),
                "created_at",
            )
        }),
        ("Customer Notes", {
            "fields": ("special_notes", "reference_images_preview")
        }),
    )

    def reference_images_preview(self, obj):
        if not obj.reference_images:
            return "-"
        thumbs = "".join(
            f'<img src="{url}" style="height:80px;width:80px;object-fit:cover;margin:4px;border:1px solid #ddd;border-radius:4px;" />'
            for url in obj.reference_images
        )
        return format_html(thumbs)

    reference_images_preview.short_description = "Reference Images"

@admin.register(Review)

class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "name", "rating", "is_visible", "created_by", "created_at")
    list_filter = ("is_visible", "rating")
    search_fields = ("product__name", "name", "comment")
    readonly_fields = ("media_preview",)
    fields = ("product", "name", "rating", "comment", "media", "media_preview", "is_visible", "created_by")

    def media_preview(self, obj):
        media = getattr(obj, "media", []) or []
        if not media:
            return "-"

        previews = []
        for item in media:
            if isinstance(item, str):
                url = item
                media_type = "image"
            else:
                url = item.get("url", "")
                media_type = item.get("type", "")
            if not url:
                continue
            if media_type == "video":
                previews.append(
                    format_html(
                        '<video src="{}" controls style="height:110px;width:150px;object-fit:cover;margin:4px;border:1px solid #ddd;border-radius:4px;"></video>',
                        url,
                    )
                )
            else:
                previews.append(
                    format_html(
                        '<img src="{}" style="height:110px;width:110px;object-fit:cover;margin:4px;border:1px solid #ddd;border-radius:4px;" />',
                        url,
                    )
                )
        return format_html_join("", "{}", ((preview,) for preview in previews)) if previews else "-"

    media_preview.short_description = "Review Media"


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "sort_order", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ('products',)


@admin.register(HeroSlide)
class HeroSlideAdmin(admin.ModelAdmin):
    list_display = ("title", "category", "is_active", "sort_order", "updated_at")
    list_filter = ("is_active", "category")
    search_fields = ("title", "subtitle", "cta_link")
    ordering = ("sort_order", "-updated_at")


class LifestyleArticleInline(admin.TabularInline):
    model = LifestyleArticle
    extra = 0
    fields = ("title", "description", "read_more_type", "read_more_url", "read_more_pdf", "is_active", "sort_order")


@admin.register(LifestyleSection)
class LifestyleSectionAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "updated_at")
    search_fields = ("title", "subtitle")
    inlines = [LifestyleArticleInline]


@admin.register(LifestyleArticle)
class LifestyleArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "section", "read_more_type", "is_active", "sort_order", "updated_at")
    list_filter = ("read_more_type", "is_active", "section")
    search_fields = ("title", "description")
    ordering = ("sort_order", "-updated_at")


@admin.register(Promotion)
class PromotionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "discount_percentage", "start_date", "end_date", "is_active", "sort_order")
    list_filter = ("is_active", "start_date", "end_date")
    search_fields = ("name", "code", "announcement_text")
    filter_horizontal = ("categories", "subcategories")
    ordering = ("sort_order", "start_date", "name")


@admin.register(AnnouncementSettings)
class AnnouncementSettingsAdmin(admin.ModelAdmin):
    list_display = ("default_text", "updated_at")

    def has_add_permission(self, request):
        if AnnouncementSettings.objects.exists():
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


# Filter System Admin


class FilterOptionInline(admin.TabularInline):
    model = FilterOption
    extra = 3
    fields = ['name', 'slug', 'color_code', 'icon_url', 'price_delta', 'is_wingback', 'display_order', 'is_active']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(FilterType)
class FilterTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'display_type', 'display_order', 'is_active', 'is_default', 'is_expanded_by_default']
    list_filter = ['display_type', 'is_active', 'is_default']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    inlines = [FilterOptionInline]
    ordering = ['display_order', 'name']


@admin.register(FilterOption)
class FilterOptionAdmin(admin.ModelAdmin):
    list_display = ['name', 'filter_type', 'slug', 'color_code', 'icon_url', 'price_delta', 'is_wingback', 'display_order', 'is_active']
    list_filter = ['filter_type', 'is_active']
    search_fields = ['name', 'slug', 'filter_type__name']
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ['filter_type']


@admin.register(CategoryFilter)
class CategoryFilterAdmin(admin.ModelAdmin):
    list_display = ['filter_type', 'category', 'subcategory', 'display_order', 'is_active']
    list_filter = ['filter_type', 'category', 'is_active']
    search_fields = ['filter_type__name', 'category__name', 'subcategory__name']
    autocomplete_fields = ['category', 'subcategory', 'filter_type']


@admin.register(ProductFilterValue)
class ProductFilterValueAdmin(admin.ModelAdmin):
    list_display = ['product', 'filter_option']
    list_filter = ['filter_option__filter_type']
    search_fields = ['product__name', 'filter_option__name']
    autocomplete_fields = ['product', 'filter_option']


# Dimension templates
class DimensionRowInline(admin.TabularInline):
    model = DimensionRow
    extra = 1
    fields = ['measurement', 'values', 'display_order']


@admin.register(DimensionTemplate)
class DimensionTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'is_default', 'updated_at']
    list_filter = ['is_default']
    prepopulated_fields = {'slug': ('name',)}
    search_fields = ['name', 'slug']
    inlines = [DimensionRowInline]


@admin.register(ProductDimensionTemplate)
class ProductDimensionTemplateAdmin(admin.ModelAdmin):
    list_display = ['product', 'template', 'allow_overrides']
    autocomplete_fields = ['product', 'template']
    search_fields = ['product__name', 'template__name']
