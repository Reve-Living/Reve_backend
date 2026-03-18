from django.db import models
from django.contrib.auth.models import User


class Category(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    description = models.TextField(blank=True)
    image = models.URLField(max_length=1000, blank=True)
    image_alt_text = models.CharField(max_length=255, blank=True, default="")
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.TextField(blank=True, default="")
    sort_order = models.IntegerField(default=0)

    def __str__(self) -> str:
        return self.name


class SubCategory(models.Model):
    category = models.ForeignKey(Category, related_name="subcategories", on_delete=models.CASCADE)
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    description = models.TextField(blank=True)
    image = models.URLField(max_length=1000, blank=True)
    image_alt_text = models.CharField(max_length=255, blank=True, default="")
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.TextField(blank=True, default="")
    sort_order = models.IntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.category.name} -> {self.name}"


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


class Product(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(unique=True, max_length=255)
    meta_title = models.CharField(max_length=255, blank=True, default="")
    meta_description = models.TextField(blank=True, default="")
    category = models.ForeignKey(Category, related_name="products", on_delete=models.CASCADE)
    subcategory = models.ForeignKey(
        SubCategory, related_name="products", on_delete=models.SET_NULL, null=True, blank=True
    )
    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_percentage = models.IntegerField(default=0)
    description = models.TextField()
    short_description = models.TextField(blank=True)
    features = models.JSONField(default=list, blank=True)
    dimensions = models.JSONField(default=list, blank=True)
    faqs = models.JSONField(default=list, blank=True)
    delivery_info = models.TextField(blank=True)
    returns_guarantee = models.TextField(blank=True)
    delivery_title = models.CharField(max_length=150, blank=True, default="")
    returns_title = models.CharField(max_length=150, blank=True, default="")
    custom_info_sections = models.JSONField(default=list, blank=True)  # list of {title, content}
    delivery_charges = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    in_stock = models.BooleanField(default=True)
    is_bestseller = models.BooleanField(default=False)
    is_new = models.BooleanField(default=False)
    # Allows per-product control over whether size option icons are displayed
    show_size_icons = models.BooleanField(default=True)
    rating = models.DecimalField(max_digits=3, decimal_places=1, default=0.0)
    review_count = models.IntegerField(default=0)
    # Optional free-form paragraph shown in place of the dimensions table when provided
    dimension_paragraph = models.TextField(blank=True, default="")
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


class ProductImage(models.Model):
    product = models.ForeignKey(Product, related_name="images", on_delete=models.CASCADE)
    url = models.URLField(max_length=1000)
    color_name = models.CharField(max_length=120, blank=True, default="")
    alt_text = models.CharField(max_length=255, blank=True, default="")


class ProductVideo(models.Model):
    product = models.ForeignKey(Product, related_name="videos", on_delete=models.CASCADE)
    url = models.URLField(max_length=1000)


class ProductColor(models.Model):
    product = models.ForeignKey(Product, related_name="colors", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    hex_code = models.CharField(max_length=7, default='#000000')
    image_url = models.URLField(max_length=1000, blank=True)
    
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
    colors = models.JSONField(default=list, blank=True)  # list of {name, hex_code, image_url?}

    class Meta:
        ordering = ["id"]


class MattressOption(models.Model):
    """
    Global mattress catalogue defined in admin (separate from mattress product category).
    Shared across all beds; per-size pricing stored in MattressOptionPrice.
    """

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image_url = models.URLField(max_length=1000, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    original_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    enable_bunk_positions = models.BooleanField(default=False)
    price_top = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_bottom = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    price_both = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    categories = models.ManyToManyField(Category, related_name="mattress_options", blank=True)
    subcategories = models.ManyToManyField(SubCategory, related_name="mattress_options", blank=True)
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

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name or f"Mattress #{self.id}"


class Order(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("shipped", "Shipped"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
    ]
    user = models.ForeignKey(User, related_name="orders", on_delete=models.SET_NULL, null=True, blank=True)
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField()
    phone = models.CharField(max_length=20)
    alternative_phone = models.CharField(max_length=20, blank=True, default="")
    address = models.TextField()
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=20)
    floor_number = models.CharField(max_length=20, blank=True, default="")
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_charges = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    payment_method = models.CharField(max_length=50)
    payment_id = models.CharField(max_length=255, blank=True)
    special_notes = models.TextField(blank=True, default="")
    reference_images = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


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


class Review(models.Model):
    product = models.ForeignKey(Product, related_name="reviews", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    rating = models.IntegerField(default=5)
    comment = models.TextField(blank=True)
    is_visible = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviews")
    created_at = models.DateTimeField(auto_now_add=True)


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
