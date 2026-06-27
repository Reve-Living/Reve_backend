from decimal import Decimal, ROUND_HALF_UP

from django.core.cache import cache
from django.contrib.auth.models import User
from django.db import models
from django.db.models import Avg, Count
from django.utils.text import slugify


def _quantize_review_rating(value) -> Decimal:
    if value is None:
        return Decimal("0.0")
    return Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


class Category(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    description = models.TextField(blank=True)
    image = models.URLField(max_length=1000, blank=True)
    show_in_collections = models.BooleanField(default=False)
    show_in_all_collections = models.BooleanField(default=False)
    image_alt_text = models.CharField(max_length=255, blank=True, default="")
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.TextField(blank=True, default="")
    sort_order = models.IntegerField(default=0)

    def __str__(self) -> str:
        return self.name


class SubCategory(models.Model):
    category = models.ForeignKey(Category, related_name="subcategories", on_delete=models.CASCADE)
    additional_categories = models.ManyToManyField(
        Category,
        related_name="shared_subcategories",
        blank=True,
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    description = models.TextField(blank=True)
    image = models.URLField(max_length=1000, blank=True)
    show_in_collections = models.BooleanField(default=False)
    show_in_all_collections = models.BooleanField(default=False)
    image_alt_text = models.CharField(max_length=255, blank=True, default="")
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.TextField(blank=True, default="")
    sort_order = models.IntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.category.name} -> {self.name}"

    def linked_category_ids(self):
        prefetched_categories = getattr(self, "_prefetched_additional_categories", None)
        if prefetched_categories is not None:
            extra_ids = [category.id for category in prefetched_categories]
        else:
            extra_ids = list(self.additional_categories.values_list("id", flat=True))
        return list(dict.fromkeys([self.category_id, *extra_ids]))

    def is_linked_to_category(self, category_or_id) -> bool:
        target_id = getattr(category_or_id, "id", category_or_id)
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return False
        return target_id in self.linked_category_ids()


class Collection(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    description = models.TextField(blank=True)
    image = models.URLField(max_length=1000, blank=True)
    is_featured = models.BooleanField(default=False)
    sort_order = models.IntegerField(default=0)
    products = models.ManyToManyField("Product", related_name="collections", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name


class HeroSlide(models.Model):
    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=500, blank=True, default="")
    category = models.ForeignKey(
        Category, related_name="hero_slides", on_delete=models.SET_NULL, null=True, blank=True
    )
    subcategory = models.ForeignKey(
        SubCategory, related_name="hero_slides", on_delete=models.SET_NULL, null=True, blank=True
    )
    selected_subcategories = models.ManyToManyField(
        SubCategory, related_name="hero_slides_selected", blank=True
    )
    cta_text = models.CharField(max_length=120, default="Shop Now")
    cta_link = models.CharField(max_length=1000, blank=True, default="")
    image = models.URLField(max_length=1000)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "-updated_at"]

    def __str__(self) -> str:
        return self.title


class LifestyleSection(models.Model):
    title = models.CharField(max_length=255, default="Transform Your Home")
    subtitle = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self) -> str:
        return self.title


class LifestyleArticle(models.Model):
    READ_MORE_NONE = "none"
    READ_MORE_URL = "url"
    READ_MORE_PDF = "pdf"
    READ_MORE_ARTICLE = "article"
    READ_MORE_TYPE_CHOICES = [
        (READ_MORE_NONE, "No read more link"),
        (READ_MORE_URL, "External/Internal URL"),
        (READ_MORE_PDF, "PDF"),
        (READ_MORE_ARTICLE, "Article page"),
    ]

    section = models.ForeignKey(LifestyleSection, related_name="articles", on_delete=models.CASCADE)
    title = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True, default="")
    description = models.TextField(blank=True, default="")
    card_image = models.URLField(max_length=1000, blank=True)
    image = models.URLField(max_length=1000, blank=True)
    article_title = models.CharField(max_length=255, blank=True, default="")
    article_intro = models.TextField(blank=True, default="")
    article_body = models.TextField(blank=True, default="")
    article_content = models.JSONField(default=list, blank=True)
    article_sections = models.JSONField(default=list, blank=True)
    read_more_type = models.CharField(max_length=10, choices=READ_MORE_TYPE_CHOICES, default=READ_MORE_NONE)
    read_more_url = models.CharField(max_length=1000, blank=True, default="")
    read_more_pdf = models.CharField(max_length=1000, blank=True, default="")
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "-updated_at", "-id"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base_slug = slugify(self.title) or "article"
            slug = base_slug
            counter = 1
            while LifestyleArticle.objects.exclude(pk=self.pk).filter(slug=slug).exists():
                slug = f"{base_slug}-{counter}"
                counter += 1
            self.slug = slug
        super().save(*args, **kwargs)


class Product(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.TextField(blank=True, default="")
    category = models.ForeignKey(Category, related_name="products", on_delete=models.CASCADE)
    subcategory = models.ForeignKey(
        SubCategory, related_name="products", on_delete=models.SET_NULL, null=True, blank=True
    )
    suggested_products = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="suggested_for_products",
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_percentage = models.IntegerField(default=0)
    description = models.TextField()
    short_description = models.TextField(blank=True)
    features = models.JSONField(default=list, blank=True)
    sofa_feature_highlights = models.JSONField(default=list, blank=True)
    dimensions = models.JSONField(default=list, blank=True)
    faqs = models.JSONField(default=list, blank=True)
    delivery_info = models.TextField(blank=True)
    returns_guarantee = models.TextField(blank=True)
    delivery_title = models.CharField(max_length=150, blank=True, default="")
    returns_title = models.CharField(max_length=150, blank=True, default="")
    custom_info_sections = models.JSONField(default=list, blank=True)  # list of {title, content}
    delivery_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    assembly_service_enabled = models.BooleanField(default=False)
    assembly_service_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    in_stock = models.BooleanField(default=True)
    is_hidden = models.BooleanField(default=False)
    is_bestseller = models.BooleanField(default=False)
    is_new = models.BooleanField(default=False)
    # Allows per-product control over whether size option icons are displayed
    show_size_icons = models.BooleanField(default=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    review_count = models.IntegerField(default=0)
    # Optional free-form paragraph shown in place of the dimensions table when provided
    dimension_paragraph = models.TextField(blank=True, default="")
    # Optional note shown below the dimensions table/modal content
    dimension_note = models.TextField(blank=True, default="")
    # Optional list of images keyed by size for the dimensions modal: [{size: "5ft King", url: "..."}]
    dimension_images = models.JSONField(default=list, blank=True)
    show_dimensions_table = models.BooleanField(default=True)
    # Manual ordering for listings (lower numbers appear first)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.name

    class Meta:
        ordering = ["sort_order", "-created_at"]
        indexes = [
            models.Index(fields=["category", "sort_order", "-created_at"], name="prod_cat_order_idx"),
            models.Index(fields=["subcategory", "sort_order", "-created_at"], name="prod_subcat_order_idx"),
            models.Index(fields=["is_hidden", "category"], name="prod_hidden_cat_idx"),
            models.Index(fields=["is_bestseller", "is_hidden"], name="prod_bestseller_idx"),
            models.Index(fields=["is_new", "is_hidden"], name="prod_new_idx"),
        ]


class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name="images", on_delete=models.CASCADE)
    url = models.URLField(max_length=1000)
    color_name = models.CharField(max_length=120, blank=True, default="")
    style_name = models.CharField(max_length=120, blank=True, default="")
    alt_text = models.CharField(max_length=255, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]


class ProductVideo(models.Model):
    product = models.ForeignKey(Product, related_name="videos", on_delete=models.CASCADE)
    url = models.URLField(max_length=1000)


class ProductColor(models.Model):
    product = models.ForeignKey(Product, related_name="colors", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    hex_code = models.CharField(max_length=7, default='#000000')
    image_url = models.URLField(max_length=1000, blank=True)
    is_available = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['id']


class ProductSize(models.Model):
    product = models.ForeignKey(Product, related_name="sizes", on_delete=models.CASCADE)
    name = models.CharField(max_length=50)
    description = models.CharField(max_length=255, blank=True)
    price_delta = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)


class ProductStyle(models.Model):
    product = models.ForeignKey(Product, related_name="styles", on_delete=models.CASCADE)
    size = models.ForeignKey(ProductSize, related_name="style_groups", on_delete=models.SET_NULL, null=True, blank=True)
    is_shared = models.BooleanField(default=False)
    name = models.CharField(max_length=100)
    # accepts full SVG markup or URL; use TextField for flexibility
    icon_url = models.TextField(blank=True, default="")
    options = models.JSONField(default=list)


class ProductFabric(models.Model):
    product = models.ForeignKey(Product, related_name="fabrics", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    image_url = models.URLField(max_length=1000)
    is_shared = models.BooleanField(default=False)
    colors = models.JSONField(default=list, blank=True)  # list of {name, hex_code, image_url?, is_available?}

    class Meta:
        ordering = ["id"]


class MattressOption(models.Model):
    """
    Global mattress catalogue defined in admin (separate from mattress product category).
    Shared across all beds; per-size pricing stored in MattressOptionPrice.
    Can also be limited to specific products within the selected category/subcategory scope.
    """

    name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True, default="")
    kids_button_label = models.CharField(max_length=120, blank=True, default="")
    description = models.TextField(blank=True)
    features = models.TextField(blank=True, default="")
    image_url = models.URLField(max_length=1000, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    original_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    enable_bunk_positions = models.BooleanField(default=False)
    price_top = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_bottom = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_both = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    categories = models.ManyToManyField(Category, related_name="mattress_options", blank=True)
    subcategories = models.ManyToManyField(SubCategory, related_name="mattress_options", blank=True)
    products = models.ManyToManyField("Product", related_name="targeted_mattress_options", blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class MattressOptionPrice(models.Model):
    """
    Size-specific pricing overrides for a MattressOption.
    """

    option = models.ForeignKey(MattressOption, related_name="prices", on_delete=models.CASCADE)
    size_label = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    original_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_top = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_bottom = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_both = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.option.name} - {self.size_label}"


class ProductMattress(models.Model):
    product = models.ForeignKey(Product, related_name="mattresses", on_delete=models.CASCADE)
    # Optional link to an existing product this mattress is based on (for reuse / import)
    source_product = models.ForeignKey(
        Product, related_name="as_mattress_option_for", on_delete=models.SET_NULL, null=True, blank=True
    )
    name = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    image_url = models.URLField(max_length=1000, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    # When true, shoppers can choose Top / Bottom / Both for bunk upgrades. "Both" charges 2x price.
    enable_bunk_positions = models.BooleanField(default=False)
    price_top = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_bottom = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_both = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    is_hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name or f"Mattress #{self.id}"


class Promotion(models.Model):
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=80, unique=True)
    announcement_text = models.CharField(max_length=255, blank=True, default="")
    discount_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    start_date = models.DateField()
    end_date = models.DateField()
    categories = models.ManyToManyField(Category, related_name="promotions", blank=True)
    subcategories = models.ManyToManyField(SubCategory, related_name="promotions", blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "start_date", "name"]

    def __str__(self):
        return f"{self.name} ({self.code})"


class AnnouncementSettings(models.Model):
    default_text = models.CharField(max_length=255, blank=True, default="Coming Soon")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Announcement settings"
        verbose_name_plural = "Announcement settings"

    def __str__(self):
        return "Announcement settings"

    @classmethod
    def get_solo(cls):
        obj = cls.objects.order_by("id").first()
        if obj:
            return obj
        return cls.objects.create()


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("shipped", "Shipped"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]
    user = models.ForeignKey(User, related_name="orders", on_delete=models.SET_NULL, null=True, blank=True)
    first_name = models.CharField(max_length=100, blank=True, default="")
    last_name = models.CharField(max_length=100, blank=True, default="")
    email = models.EmailField(blank=True, default="")
    phone = models.CharField(max_length=20, blank=True, default="")
    alternative_phone = models.CharField(max_length=20, blank=True, default="")
    address = models.TextField(blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    postal_code = models.CharField(max_length=20, blank=True, default="")
    floor_number = models.CharField(max_length=20, blank=True, default="")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_charges = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    payment_method = models.CharField(max_length=50)
    payment_id = models.CharField(max_length=255, blank=True)
    payment_metadata = models.JSONField(default=dict, blank=True)
    promo_code = models.CharField(max_length=80, blank=True, default="")
    promo_name = models.CharField(max_length=255, blank=True, default="")
    promo_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    special_notes = models.TextField(blank=True, default="")
    reference_images = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmation_email_sent_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    refund_status = models.CharField(max_length=20, blank=True, default="")
    refund_provider = models.CharField(max_length=20, blank=True, default="")
    refund_id = models.CharField(max_length=255, blank=True, default="")
    refund_error = models.TextField(blank=True, default="")
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    refunded_at = models.DateTimeField(null=True, blank=True)


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    quantity = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    size = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=50, blank=True)
    style = models.TextField(blank=True, default="")
    dimension = models.CharField(max_length=120, blank=True, default="")
    dimension_details = models.TextField(blank=True, default="")
    selected_variants = models.JSONField(default=dict, blank=True)
    extras_total = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    include_dimension = models.BooleanField(default=True)
    assembly_service_selected = models.BooleanField(default=False)
    assembly_service_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)


class Review(models.Model):
    product = models.ForeignKey(Product, related_name="reviews", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    rating = models.IntegerField(default=5)
    comment = models.TextField(blank=True)
    media = models.JSONField(default=list, blank=True)
    is_visible = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviews")
    created_at = models.DateTimeField(auto_now_add=True)

    @staticmethod
    def sync_product_summary(product_id):
        if not product_id:
            return

        aggregates = Review.objects.filter(product_id=product_id, is_visible=True).aggregate(
            average_rating=Avg("rating"),
            total_reviews=Count("id"),
        )
        Product.objects.filter(pk=product_id).update(
            rating=_quantize_review_rating(aggregates.get("average_rating")),
            review_count=int(aggregates.get("total_reviews") or 0),
        )
        cache.clear()

    def save(self, *args, **kwargs):
        previous_product_id = None
        if self.pk:
            previous_product_id = (
                type(self).objects.filter(pk=self.pk).values_list("product_id", flat=True).first()
            )

        super().save(*args, **kwargs)

        self.sync_product_summary(self.product_id)
        if previous_product_id and previous_product_id != self.product_id:
            self.sync_product_summary(previous_product_id)

    def delete(self, *args, **kwargs):
        product_id = self.product_id
        super().delete(*args, **kwargs)
        self.sync_product_summary(product_id)


class FilterType(models.Model):
    """
    Defines the type of filter (e.g., Bed Size, Colour, Fabric Type)
    This is the "group" or "category" of filter options
    """
    FILTER_DISPLAY_TYPES = [
        ('checkbox', 'Checkbox List'),
        ('color_swatch', 'Color Swatch'),
        ('radio', 'Radio Buttons'),
        ('dropdown', 'Dropdown Select'),
    ]
    
    name = models.CharField(max_length=100)  # e.g., "Bed Size", "Colour"
    slug = models.SlugField(unique=True, max_length=255)  # e.g., "bed-size", "colour"
    display_type = models.CharField(max_length=20, choices=FILTER_DISPLAY_TYPES, default='checkbox')
    display_order = models.PositiveIntegerField(default=0)  # For ordering filters in sidebar
    is_active = models.BooleanField(default=True)
    is_expanded_by_default = models.BooleanField(default=True)  # Show expanded or collapsed
    icon_url = models.URLField(max_length=1000, blank=True, default="")  # SVG or image used in UI
    display_hint = models.CharField(max_length=255, blank=True, default="")  # short helper text
    is_default = models.BooleanField(default=False)  # lets admin flag Size / Price, etc., for priority UI
    
    class Meta:
        ordering = ['display_order', 'name']
    
    def __str__(self):
        return self.name


class FilterOption(models.Model):
    """
    Individual filter options within a FilterType
    e.g., "Small Single", "Single", "Double" under "Bed Size"
    """
    filter_type = models.ForeignKey(FilterType, on_delete=models.CASCADE, related_name='options')
    name = models.CharField(max_length=100)  # e.g., "Small Single", "Plush Velvet"
    slug = models.SlugField(max_length=255)  # e.g., "small-single", "plush-velvet"
    value = models.CharField(max_length=100, blank=True)  # Optional: for special values
    color_code = models.CharField(max_length=7, blank=True, null=True)  # Hex color for color swatches
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    icon_url = models.URLField(max_length=1000, blank=True, default="")  # SVG/icon
    price_delta = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    is_wingback = models.BooleanField(default=False)  # width +4cm note
    metadata = models.JSONField(default=dict, blank=True)  # free-form option metadata
    
    class Meta:
        ordering = ['display_order', 'name']
        unique_together = ['filter_type', 'slug']
    
    def __str__(self):
        return f"{self.filter_type.name} - {self.name}"


class CategoryFilter(models.Model):
    """
    Links FilterTypes to Categories/SubCategories
    This determines which filters appear on which category pages
    """
    category = models.ForeignKey(
        Category, 
        on_delete=models.CASCADE, 
        related_name='category_filters',
        null=True, 
        blank=True
    )
    subcategory = models.ForeignKey(
        SubCategory, 
        on_delete=models.CASCADE, 
        related_name='subcategory_filters',
        null=True, 
        blank=True
    )
    filter_type = models.ForeignKey(FilterType, on_delete=models.CASCADE)
    display_order = models.PositiveIntegerField(default=0)  # Order within this category
    is_active = models.BooleanField(default=True)
    
    class Meta:
        ordering = ['display_order']
    
    def __str__(self):
        target = self.subcategory.name if self.subcategory else self.category.name
        return f"{self.filter_type.name} -> {target}"


class ProductFilterValue(models.Model):
    """
    Links Products to their filter option values
    A product can have multiple filter options (e.g., available in multiple colors)
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='filter_values')
    filter_option = models.ForeignKey(FilterOption, on_delete=models.CASCADE)
    
    class Meta:
        unique_together = ['product', 'filter_option']
        indexes = [
            models.Index(fields=["filter_option", "product"], name="pfv_option_product_idx"),
            models.Index(fields=["product", "filter_option"], name="pfv_product_option_idx"),
        ]
    
    def __str__(self):
        return f"{self.product.name} - {self.filter_option}"


# Dimension templates & rows allow reusable size charts per product
class DimensionTemplate(models.Model):
    name = models.CharField(max_length=150)
    slug = models.SlugField(unique=True, max_length=255)
    notes = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class DimensionRow(models.Model):
    template = models.ForeignKey(DimensionTemplate, related_name="rows", on_delete=models.CASCADE)
    measurement = models.CharField(max_length=100)  # e.g., Length, Width
    values = models.JSONField(default=dict, blank=True)  # {"3ft Single": "215 cm (84.6\")", ...}
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]

    def __str__(self):
        return f"{self.template.name}: {self.measurement}"


# Direct link from product to a dimension template (can still override via Product.dimensions)
class ProductDimensionTemplate(models.Model):
    product = models.OneToOneField(Product, related_name="dimension_template_link", on_delete=models.CASCADE)
    template = models.ForeignKey(DimensionTemplate, related_name="product_links", on_delete=models.CASCADE)
    allow_overrides = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.product.name} -> {self.template.name}"
