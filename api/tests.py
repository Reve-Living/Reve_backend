from django.core import mail
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from .models import (
    Category,
    Product,
    SubCategory,
    ProductImage,
    ProductColor,
    ProductFabric,
    ProductMattress,
    ProductSize,
    ProductStyle,
    ProductFilterValue,
    FilterType,
    FilterOption,
    DimensionTemplate,
    MattressOption,
    ProductDimensionTemplate,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="smtp.hostinger.com",
    DEFAULT_FROM_EMAIL="info@reveliving.co.uk",
    ORDER_NOTIFICATION_EMAIL="info@reveliving.co.uk",
)
class OrderEmailTests(TestCase):
    def test_order_creation_sends_customer_and_admin_emails(self):
        client = APIClient()
        payload = {
            "first_name": "Ayesha",
            "last_name": "Jahangir",
            "email": "customer@example.com",
            "phone": "+44 1234 567890",
            "alternative_phone": "+44 7000 000000",
            "address": "221B Baker Street",
            "city": "London",
            "postal_code": "NW1 6XE",
            "floor_number": "2",
            "total_amount": "599.99",
            "delivery_charges": "0.00",
            "payment_method": "paypal",
            "special_notes": "Please call before delivery.",
            "reference_images": [
                "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn4nVQAAAAASUVORK5CYII="
            ],
            "items": [
                {
                    "quantity": 2,
                    "price": "299.99",
                    "size": "King",
                    "color": "Cream",
                    "style": "Buttoned headboard",
                    "dimension": "Custom height",
                    "dimension_details": "54 inches tall",
                    "selected_variants": {"Fabric": "Plush Velvet", "Storage": "Ottoman"},
                    "extras_total": "25.00",
                    "include_dimension": True,
                    "assembly_service_selected": True,
                    "assembly_service_price": "49.00",
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = client.post("/api/orders/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 2)

        recipients = sorted(message.to[0] for message in mail.outbox)
        self.assertEqual(recipients, ["customer@example.com", "info@reveliving.co.uk"])

        customer_email = next(message for message in mail.outbox if message.to == ["customer@example.com"])
        order_id = response.data["id"]
        self.assertEqual(customer_email.subject, f"Order Confirmation - Reve Living (Order #{order_id})")
        self.assertNotIn("2. EMAIL AUTOMATION - CUSTOMER ORDER CONFIRMATION", customer_email.body)
        self.assertNotIn("Subject: Order Confirmation", customer_email.body)
        self.assertIn("Ayesha Jahangir", customer_email.body)
        self.assertIn("Product Name | Quantity | Price", customer_email.body)
        self.assertIn("Payment Method: PayPal", customer_email.body)
        self.assertIn("Assembly Service: £49.00", customer_email.body)
        self.assertIn("Email: support@reveliving.co.uk", customer_email.body)
        self.assertIn("Phone: +44 7386 340475", customer_email.body)
        self.assertEqual(len(customer_email.attachments), 1)

        order_item = response.data["items"][0]
        self.assertTrue(order_item["assembly_service_selected"])
        self.assertEqual(order_item["assembly_service_price"], "49.00")

    def test_admin_can_download_delivery_note_pdf(self):
        client = APIClient()
        payload = {
            "first_name": "Ayesha",
            "last_name": "Jahangir",
            "email": "customer@example.com",
            "phone": "+44 1234 567890",
            "alternative_phone": "+44 7000 000000",
            "address": "221B Baker Street",
            "city": "London",
            "postal_code": "NW1 6XE",
            "floor_number": "2",
            "total_amount": "599.99",
            "delivery_charges": "0.00",
            "payment_method": "paypal",
            "special_notes": "Leave at reception.",
            "items": [
                {
                    "quantity": 2,
                    "price": "299.99",
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            create_response = client.post("/api/orders/", payload, format="json")

        admin_user = User.objects.create_user(
            username="admin",
            password="password123",
            email="admin@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        pdf_response = client.get(f"/api/orders/{create_response.data['id']}/delivery_note_pdf/")

        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertIn("attachment;", pdf_response["Content-Disposition"])
        self.assertTrue(pdf_response.content.startswith(b"%PDF"))


class CategorySortOrderSwapTests(TestCase):
    def test_updating_category_sort_order_swaps_with_existing_category(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin",
            password="password123",
            email="admin@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        sofas = Category.objects.create(name="Sofas", slug="sofas", sort_order=3)
        tables = Category.objects.create(name="Tables", slug="tables", sort_order=5)

        response = client.put(
            f"/api/categories/{tables.id}/",
            {
                "name": "Tables",
                "slug": "tables",
                "description": "",
                "image": "",
                "show_in_collections": False,
                "image_alt_text": "",
                "meta_title": "",
                "meta_description": "",
                "sort_order": 3,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        sofas.refresh_from_db()
        tables.refresh_from_db()

        self.assertEqual(tables.sort_order, 3)
        self.assertEqual(sofas.sort_order, 5)


class SharedSubcategoryTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username="admin-shared-sub",
            password="password123",
            email="admin-shared@example.com",
            is_staff=True,
        )
        self.client.force_authenticate(user=self.admin_user)

    def test_category_list_includes_shared_subcategory(self):
        beds = Category.objects.create(name="Beds", slug="beds-shared", sort_order=1)
        sale = Category.objects.create(name="Sale", slug="sale-shared", sort_order=2)
        subcategory = SubCategory.objects.create(name="Ottoman Beds", slug="ottoman-shared", category=beds)
        subcategory.additional_categories.add(sale)

        response = self.client.get("/api/categories/")

        self.assertEqual(response.status_code, 200)
        sale_payload = next(item for item in response.data if item["id"] == sale.id)
        self.assertEqual([sub["id"] for sub in sale_payload["subcategories"]], [subcategory.id])

    def test_subcategory_filter_includes_shared_category(self):
        beds = Category.objects.create(name="Beds", slug="beds-filter-shared", sort_order=1)
        sale = Category.objects.create(name="Sale", slug="sale-filter-shared", sort_order=2)
        subcategory = SubCategory.objects.create(name="Storage Beds", slug="storage-shared", category=beds)
        subcategory.additional_categories.add(sale)

        response = self.client.get(f"/api/subcategories/?category={sale.id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [subcategory.id])

    def test_unlinking_primary_category_promotes_other_link_instead_of_deleting_subcategory(self):
        beds = Category.objects.create(name="Beds", slug="beds-unlink", sort_order=1)
        sale = Category.objects.create(name="Sale", slug="sale-unlink", sort_order=2)
        subcategory = SubCategory.objects.create(name="Luxury Beds", slug="luxury-beds-unlink", category=beds)
        subcategory.additional_categories.add(sale)

        response = self.client.post(
            f"/api/subcategories/{subcategory.id}/unlink-category/",
            {"category": beds.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        subcategory.refresh_from_db()
        self.assertEqual(subcategory.category_id, sale.id)
        self.assertEqual(list(subcategory.additional_categories.values_list("id", flat=True)), [])


class ProductDuplicateTests(TestCase):
    def test_admin_can_duplicate_product_with_related_records(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-duplicate",
            password="password123",
            email="admin-duplicate@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-duplicate", sort_order=1)
        product = Product.objects.create(
            name="Aria Bed",
            slug="aria-bed",
            category=category,
            price="499.99",
            short_description="Original short description",
            description="Original long description",
            in_stock=True,
            is_hidden=False,
            delivery_info="Delivery included",
            sort_order=3,
        )
        ProductImage.objects.create(product=product, url="https://example.com/bed.jpg", alt_text="Main image")
        ProductColor.objects.create(
            product=product,
            name="Tan",
            hex_code="#D2B48C",
            image_url="https://example.com/tan.jpg",
            is_available=False,
        )
        ProductFabric.objects.create(
            product=product,
            name="Leather",
            image_url="https://example.com/leather.jpg",
            colors=[
                {
                    "name": "Whiskey",
                    "hex_code": "#8B5E3C",
                    "image_url": "https://example.com/whiskey.jpg",
                    "is_available": False,
                },
                {
                    "name": "Stone",
                    "hex_code": "#B7B1A1",
                    "image_url": "https://example.com/stone.jpg",
                    "is_available": True,
                },
            ],
        )
        size = ProductSize.objects.create(product=product, name="King", description="5ft", price_delta="25.00")
        ProductStyle.objects.create(
            product=product,
            size=size,
            name="Wingback",
            icon_url="<svg></svg>",
            options=[{"label": "Tall", "price_delta": 10}],
        )
        filter_type = FilterType.objects.create(name="Color", slug="color-duplicate")
        filter_option = FilterOption.objects.create(
            filter_type=filter_type,
            name="Cream",
            slug="cream-duplicate",
        )
        ProductFilterValue.objects.create(product=product, filter_option=filter_option)
        template = DimensionTemplate.objects.create(name="Bed Template", slug="bed-template-duplicate")
        ProductDimensionTemplate.objects.create(product=product, template=template, allow_overrides=True)

        response = client.post(f"/api/products/{product.id}/duplicate/", format="json")

        self.assertEqual(response.status_code, 201)
        duplicated_id = response.data["id"]
        self.assertNotEqual(duplicated_id, product.id)

        duplicated = Product.objects.get(pk=duplicated_id)
        self.assertEqual(duplicated.name, "Aria Bed (Copy)")
        self.assertTrue(duplicated.slug.startswith("aria-bed-copy"))
        self.assertTrue(duplicated.is_hidden)
        self.assertEqual(duplicated.sort_order, 0)
        self.assertEqual(duplicated.rating, 0)
        self.assertEqual(duplicated.review_count, 0)
        self.assertEqual(duplicated.images.count(), 1)
        self.assertEqual(duplicated.colors.count(), 1)
        self.assertEqual(duplicated.fabrics.count(), 1)
        self.assertEqual(duplicated.sizes.count(), 1)
        self.assertEqual(duplicated.styles.count(), 1)
        self.assertEqual(duplicated.filter_values.count(), 1)
        self.assertTrue(hasattr(duplicated, "dimension_template_link"))
        self.assertEqual(duplicated.dimension_template_link.template_id, template.id)
        self.assertNotEqual(duplicated.sizes.first().id, size.id)
        self.assertEqual(duplicated.styles.first().size_id, duplicated.sizes.first().id)
        self.assertFalse(duplicated.colors.first().is_available)
        self.assertFalse(duplicated.fabrics.first().colors[0]["is_available"])


class ProductVariantAvailabilityTests(TestCase):
    def test_admin_can_save_availability_for_colors_and_fabric_colors(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-availability",
            password="password123",
            email="admin-availability@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Sofas", slug="sofas-availability", sort_order=1)

        response = client.post(
            "/api/products/",
            {
                "name": "Turin Sofa",
                "slug": "turin-sofa",
                "category": category.id,
                "subcategory": None,
                "price": "899.99",
                "original_price": None,
                "discount_percentage": 0,
                "description": "Turin sofa description",
                "short_description": "Turin sofa",
                "features": [],
                "dimensions": [],
                "dimension_images": [],
                "show_dimensions_table": True,
                "faqs": [],
                "delivery_info": "",
                "returns_guarantee": "",
                "delivery_title": "",
                "returns_title": "",
                "custom_info_sections": [],
                "delivery_charges": "0.00",
                "in_stock": True,
                "is_bestseller": False,
                "is_new": False,
                "show_size_icons": True,
                "sort_order": 1,
                "rating": "0.0",
                "review_count": 0,
                "colors": [
                    {
                        "name": "Cream",
                        "hex_code": "#F5F5DC",
                        "image_url": "https://example.com/cream.jpg",
                        "is_available": False,
                    }
                ],
                "fabrics": [
                    {
                        "name": "Leather",
                        "image_url": "https://example.com/leather.jpg",
                        "is_shared": False,
                        "colors": [
                            {
                                "name": "Whiskey",
                                "hex_code": "#8B5E3C",
                                "image_url": "https://example.com/whiskey.jpg",
                                "is_available": False,
                            },
                            {
                                "name": "Stone",
                                "hex_code": "#B7B1A1",
                                "image_url": "https://example.com/stone.jpg",
                                "is_available": True,
                            },
                        ],
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["colors"][0]["is_available"], False)
        self.assertEqual(response.data["fabrics"][0]["colors"][0]["is_available"], False)
        self.assertEqual(response.data["fabrics"][0]["colors"][1]["is_available"], True)

        product = Product.objects.get(pk=response.data["id"])
        self.assertFalse(product.colors.first().is_available)
        self.assertFalse(product.fabrics.first().colors[0]["is_available"])


class ProductImageOrderTests(TestCase):
    def test_product_images_are_returned_in_sort_order(self):
        category = Category.objects.create(name="Beds", slug="beds-images-order", sort_order=1)
        product = Product.objects.create(
            name="Image Order Bed",
            slug="image-order-bed",
            category=category,
            price="499.99",
            short_description="Short",
            description="Long",
        )
        ProductImage.objects.create(product=product, url="https://example.com/third.jpg", sort_order=3)
        ProductImage.objects.create(product=product, url="https://example.com/first.jpg", sort_order=1)
        ProductImage.objects.create(product=product, url="https://example.com/second.jpg", sort_order=2)

        response = APIClient().get(f"/api/products/?slug={product.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [image["url"] for image in response.data[0]["images"]],
            [
                "https://example.com/first.jpg",
                "https://example.com/second.jpg",
                "https://example.com/third.jpg",
            ],
        )

    def test_product_detail_includes_admin_selected_suggested_products(self):
        category = Category.objects.create(name="Beds", slug="beds-suggested", sort_order=1)
        product = Product.objects.create(
            name="Main Bed",
            slug="main-bed",
            category=category,
            price="499.99",
            short_description="Short",
            description="Long",
        )
        suggested = Product.objects.create(
            name="Suggested Bed",
            slug="suggested-bed",
            category=category,
            price="599.99",
            short_description="Suggested",
            description="Suggested description",
        )
        hidden = Product.objects.create(
            name="Hidden Suggested Bed",
            slug="hidden-suggested-bed",
            category=category,
            price="699.99",
            short_description="Hidden",
            description="Hidden description",
            is_hidden=True,
        )
        product.suggested_products.set([suggested, hidden])

        response = APIClient().get(f"/api/products/?slug={product.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(response.data[0]["suggested_products"], [suggested.id, hidden.id])
        self.assertEqual([item["id"] for item in response.data[0]["suggested_products_data"]], [suggested.id])


class ProductSortOrderSwapTests(TestCase):
    def test_public_product_list_hides_hidden_products(self):
        client = APIClient()
        category = Category.objects.create(name="Sofas", slug="sofas", sort_order=1)

        visible = Product.objects.create(
            name="Visible Sofa",
            slug="visible-sofa",
            category=category,
            price="499.99",
            short_description="Visible",
            description="Visible sofa description",
            is_hidden=False,
        )
        Product.objects.create(
            name="Hidden Sofa",
            slug="hidden-sofa",
            category=category,
            price="599.99",
            short_description="Hidden",
            description="Hidden sofa description",
            is_hidden=True,
        )

        response = client.get(f"/api/products/?category={category.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [visible.id])

    def test_admin_product_list_includes_hidden_products(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-hidden",
            password="password123",
            email="admin-hidden@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Sofas", slug="sofas-admin", sort_order=1)
        visible = Product.objects.create(
            name="Visible Admin Sofa",
            slug="visible-admin-sofa",
            category=category,
            price="499.99",
            short_description="Visible",
            description="Visible admin sofa description",
            is_hidden=False,
        )
        hidden = Product.objects.create(
            name="Hidden Admin Sofa",
            slug="hidden-admin-sofa",
            category=category,
            price="599.99",
            short_description="Hidden",
            description="Hidden admin sofa description",
            is_hidden=True,
        )

        response = client.get(f"/api/products/?category={category.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual({item["id"] for item in response.data}, {visible.id, hidden.id})

    def test_product_list_places_zero_sort_order_before_positive(self):
        client = APIClient()
        category = Category.objects.create(name="Beds", slug="beds", sort_order=1)

        Product.objects.create(
            name="Zero Order Bed",
            slug="zero-order-bed",
            category=category,
            price="499.99",
            short_description="Zero",
            description="Zero order description",
            sort_order=0,
        )
        prioritized = Product.objects.create(
            name="Priority Bed",
            slug="priority-bed",
            category=category,
            price="599.99",
            short_description="Priority",
            description="Priority order description",
            sort_order=1,
        )

        response = client.get(f"/api/products/?category={category.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 2)
        self.assertEqual(response.data[0]["slug"], "zero-order-bed")

    def test_creating_product_sort_order_swaps_with_existing_product(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin",
            password="password123",
            email="admin@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds", sort_order=1)
        first_product = Product.objects.create(
            name="Oxford Bed",
            slug="oxford-bed",
            category=category,
            price="499.99",
            short_description="Oxford",
            description="Oxford bed description",
            sort_order=1,
        )

        response = client.post(
            "/api/products/",
            {
                "name": "Cambridge Bed",
                "slug": "cambridge-bed",
                "category": category.id,
                "subcategory": None,
                "price": "599.99",
                "original_price": None,
                "discount_percentage": 0,
                "description": "Cambridge bed description",
                "short_description": "Cambridge",
                "features": [],
                "dimensions": [],
                "dimension_images": [],
                "show_dimensions_table": True,
                "faqs": [],
                "delivery_info": "",
                "returns_guarantee": "",
                "delivery_title": "",
                "returns_title": "",
                "custom_info_sections": [],
                "delivery_charges": "0.00",
                "in_stock": True,
                "is_bestseller": False,
                "is_new": False,
                "show_size_icons": True,
                "sort_order": 1,
                "rating": "0.0",
                "review_count": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created_product = Product.objects.get(id=response.data["id"])
        first_product.refresh_from_db()

        self.assertEqual(created_product.sort_order, 1)
        self.assertEqual(first_product.sort_order, 2)

    def test_updating_product_sort_order_swaps_with_existing_product(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin",
            password="password123",
            email="admin@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds", sort_order=1)
        first_product = Product.objects.create(
            name="Oxford Bed",
            slug="oxford-bed",
            category=category,
            price="499.99",
            short_description="Oxford",
            description="Oxford bed description",
            sort_order=1,
        )
        second_product = Product.objects.create(
            name="Cambridge Bed",
            slug="cambridge-bed",
            category=category,
            price="599.99",
            short_description="Cambridge",
            description="Cambridge bed description",
            sort_order=5,
        )

        response = client.put(
            f"/api/products/{second_product.id}/",
            {
                "name": second_product.name,
                "slug": second_product.slug,
                "category": category.id,
                "subcategory": None,
                "price": "599.99",
                "original_price": None,
                "discount_percentage": 0,
                "description": second_product.description,
                "short_description": second_product.short_description,
                "features": [],
                "dimensions": [],
                "dimension_images": [],
                "show_dimensions_table": True,
                "faqs": [],
                "delivery_info": "",
                "returns_guarantee": "",
                "delivery_title": "",
                "returns_title": "",
                "custom_info_sections": [],
                "delivery_charges": "0.00",
                "in_stock": True,
                "is_bestseller": False,
                "is_new": False,
                "show_size_icons": True,
                "sort_order": 1,
                "rating": "0.0",
                "review_count": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        first_product.refresh_from_db()
        second_product.refresh_from_db()

        self.assertEqual(second_product.sort_order, 1)
        self.assertEqual(first_product.sort_order, 2)

    def test_creating_product_sort_order_uses_requested_position_even_with_gaps(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin2",
            password="password123",
            email="admin2@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-gap-create", sort_order=1)
        first_product = Product.objects.create(
            name="First Bed",
            slug="first-bed-gap-create",
            category=category,
            price="499.99",
            short_description="First",
            description="First bed description",
            sort_order=1,
        )
        third_product = Product.objects.create(
            name="Third Bed",
            slug="third-bed-gap-create",
            category=category,
            price="699.99",
            short_description="Third",
            description="Third bed description",
            sort_order=3,
        )

        response = client.post(
            "/api/products/",
            {
                "name": "Second Bed",
                "slug": "second-bed-gap-create",
                "category": category.id,
                "subcategory": None,
                "price": "599.99",
                "original_price": None,
                "discount_percentage": 0,
                "description": "Second bed description",
                "short_description": "Second",
                "features": [],
                "dimensions": [],
                "dimension_images": [],
                "show_dimensions_table": True,
                "faqs": [],
                "delivery_info": "",
                "returns_guarantee": "",
                "delivery_title": "",
                "returns_title": "",
                "custom_info_sections": [],
                "delivery_charges": "0.00",
                "in_stock": True,
                "is_bestseller": False,
                "is_new": False,
                "show_size_icons": True,
                "sort_order": 2,
                "rating": "0.0",
                "review_count": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        second_product = Product.objects.get(id=response.data["id"])
        first_product.refresh_from_db()
        third_product.refresh_from_db()

        self.assertEqual(first_product.sort_order, 1)
        self.assertEqual(second_product.sort_order, 2)
        self.assertEqual(third_product.sort_order, 3)

    def test_updating_product_sort_order_keeps_requested_position_even_with_gaps(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin3",
            password="password123",
            email="admin3@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-gap-update", sort_order=1)
        first_product = Product.objects.create(
            name="First Bed Update",
            slug="first-bed-gap-update",
            category=category,
            price="499.99",
            short_description="First",
            description="First bed description",
            sort_order=1,
        )
        moving_product = Product.objects.create(
            name="Moving Bed Update",
            slug="moving-bed-gap-update",
            category=category,
            price="599.99",
            short_description="Moving",
            description="Moving bed description",
            sort_order=5,
        )
        trailing_product = Product.objects.create(
            name="Trailing Bed Update",
            slug="trailing-bed-gap-update",
            category=category,
            price="699.99",
            short_description="Trailing",
            description="Trailing bed description",
            sort_order=9,
        )

        response = client.put(
            f"/api/products/{trailing_product.id}/",
            {
                "name": trailing_product.name,
                "slug": trailing_product.slug,
                "category": category.id,
                "subcategory": None,
                "price": "699.99",
                "original_price": None,
                "discount_percentage": 0,
                "description": trailing_product.description,
                "short_description": trailing_product.short_description,
                "features": [],
                "dimensions": [],
                "dimension_images": [],
                "show_dimensions_table": True,
                "faqs": [],
                "delivery_info": "",
                "returns_guarantee": "",
                "delivery_title": "",
                "returns_title": "",
                "custom_info_sections": [],
                "delivery_charges": "0.00",
                "in_stock": True,
                "is_bestseller": False,
                "is_new": False,
                "show_size_icons": True,
                "sort_order": 2,
                "rating": "0.0",
                "review_count": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        first_product.refresh_from_db()
        moving_product.refresh_from_db()
        trailing_product.refresh_from_db()

        self.assertEqual(first_product.sort_order, 1)
        self.assertEqual(trailing_product.sort_order, 2)
        self.assertEqual(moving_product.sort_order, 5)

    def test_updating_unsorted_product_allows_direct_gap_position(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin6",
            password="password123",
            email="admin6@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-direct-gap-update", sort_order=1)
        first_product = Product.objects.create(
            name="Gapless Bed One",
            slug="gapless-bed-one",
            category=category,
            price="499.99",
            short_description="One",
            description="Gapless one description",
            sort_order=0,
        )
        second_product = Product.objects.create(
            name="Gapless Bed Two",
            slug="gapless-bed-two",
            category=category,
            price="599.99",
            short_description="Two",
            description="Gapless two description",
            sort_order=0,
        )

        response = client.patch(
            f"/api/products/{first_product.id}/",
            {"sort_order": 5},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        first_product.refresh_from_db()
        second_product.refresh_from_db()

        self.assertEqual(first_product.sort_order, 5)
        self.assertEqual(second_product.sort_order, 0)

    def test_editing_product_without_sort_change_preserves_existing_positions(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin6b",
            password="password123",
            email="admin6b@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-stable-edit-order", sort_order=1)
        first_product = Product.objects.create(
            name="Stable Bed One",
            slug="stable-bed-one",
            category=category,
            price="499.99",
            short_description="One",
            description="Stable one description",
            sort_order=1,
        )
        second_product = Product.objects.create(
            name="Stable Bed Two",
            slug="stable-bed-two",
            category=category,
            price="599.99",
            short_description="Two",
            description="Stable two description",
            sort_order=5,
        )

        response = client.patch(
            f"/api/products/{second_product.id}/",
            {"name": "Stable Bed Two Updated"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        first_product.refresh_from_db()
        second_product.refresh_from_db()

        self.assertEqual(first_product.sort_order, 1)
        self.assertEqual(second_product.sort_order, 5)
        self.assertEqual(second_product.name, "Stable Bed Two Updated")

    def test_creating_product_allows_direct_gap_position(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin7",
            password="password123",
            email="admin7@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-direct-gap-create", sort_order=1)

        response = client.post(
            "/api/products/",
            {
                "name": "Direct Gap Bed",
                "slug": "direct-gap-bed",
                "category": category.id,
                "subcategory": None,
                "price": "599.99",
                "original_price": None,
                "discount_percentage": 0,
                "description": "Direct gap bed description",
                "short_description": "Direct gap",
                "features": [],
                "dimensions": [],
                "dimension_images": [],
                "show_dimensions_table": True,
                "faqs": [],
                "delivery_info": "",
                "returns_guarantee": "",
                "delivery_title": "",
                "returns_title": "",
                "custom_info_sections": [],
                "delivery_charges": "0.00",
                "in_stock": True,
                "is_bestseller": False,
                "is_new": False,
                "show_size_icons": True,
                "sort_order": 6,
                "rating": "0.0",
                "review_count": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created_product = Product.objects.get(id=response.data["id"])
        self.assertEqual(created_product.sort_order, 6)

    def test_updating_product_sort_order_shifts_intermediate_products_when_moving_later(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin4",
            password="password123",
            email="admin4@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-shift-later", sort_order=1)
        products = []
        for index in range(1, 7):
            products.append(
                Product.objects.create(
                    name=f"Bed {index}",
                    slug=f"bed-shift-later-{index}",
                    category=category,
                    price="499.99",
                    short_description=f"Bed {index}",
                    description=f"Bed {index} description",
                    sort_order=index,
                )
            )

        moving_product = products[0]

        response = client.patch(
            f"/api/products/{moving_product.id}/",
            {"sort_order": 5},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        for product in products:
            product.refresh_from_db()

        self.assertEqual(products[1].sort_order, 1)
        self.assertEqual(products[2].sort_order, 2)
        self.assertEqual(products[3].sort_order, 3)
        self.assertEqual(products[4].sort_order, 4)
        self.assertEqual(moving_product.sort_order, 5)
        self.assertEqual(products[5].sort_order, 6)

    def test_updating_product_sort_order_shifts_intermediate_products_when_moving_to_later_gap(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin5",
            password="password123",
            email="admin5@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-shift-gap", sort_order=1)
        products = []
        for index in range(1, 15):
            products.append(
                Product.objects.create(
                    name=f"Gap Bed {index}",
                    slug=f"bed-shift-gap-{index}",
                    category=category,
                    price="499.99",
                    short_description=f"Gap Bed {index}",
                    description=f"Gap Bed {index} description",
                    sort_order=index,
                )
            )

        moving_product = products[4]

        response = client.patch(
            f"/api/products/{moving_product.id}/",
            {"sort_order": 14},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        for product in products:
            product.refresh_from_db()

        self.assertEqual(products[5].sort_order, 5)
        self.assertEqual(products[6].sort_order, 6)
        self.assertEqual(products[12].sort_order, 12)
        self.assertEqual(products[13].sort_order, 13)
        self.assertEqual(moving_product.sort_order, 14)

    def test_updating_product_sort_order_uses_absolute_position_within_subcategory(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin6",
            password="password123",
            email="admin6@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Tables", slug="tables-absolute-position", sort_order=1)
        subcategory = SubCategory.objects.create(
            name="Coffee Tables",
            slug="coffee-tables-absolute-position",
            category=category,
            sort_order=1,
        )

        products = []
        for index in range(1, 25):
            products.append(
                Product.objects.create(
                    name=f"Table {index}",
                    slug=f"table-absolute-position-{index}",
                    category=category,
                    subcategory=subcategory,
                    price="199.99",
                    short_description=f"Table {index}",
                    description=f"Table {index} description",
                    sort_order=0,
                )
            )

        moving_product = products[0]

        response = client.patch(
            f"/api/products/{moving_product.id}/",
            {"sort_order": 23},
            format="json",
        )

        self.assertEqual(response.status_code, 200)

        ordered_ids = list(
            Product.objects.filter(category=category, subcategory=subcategory)
            .order_by("sort_order", "-created_at", "-id")
            .values_list("id", flat=True)
        )

        self.assertEqual(len(ordered_ids), 24)
        self.assertEqual(ordered_ids[22], moving_product.id)


class ProductMattressVisibilityTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin_user = User.objects.create_user(
            username="admin-mattress-visibility",
            password="password123",
            email="admin-mattress@example.com",
            is_staff=True,
        )
        self.category = Category.objects.create(name="Beds", slug="beds-mattress-visibility", sort_order=1)
        self.product = Product.objects.create(
            name="Luna Bed",
            slug="luna-bed-mattress-visibility",
            category=self.category,
            price="799.99",
            short_description="Luxury bed",
            description="Luxury bed description",
            in_stock=True,
            is_hidden=False,
        )
        self.hidden_option = MattressOption.objects.create(
            name="Semi Orthopaedic Mattress",
            description="Supportive feel",
            price="0.00",
        )
        self.hidden_option.categories.add(self.category)
        self.visible_option = MattressOption.objects.create(
            name="Medium Firm Everyday Support",
            description="Everyday comfort",
            price="49.99",
        )
        self.visible_option.categories.add(self.category)

    def test_public_product_detail_omits_hidden_mattresses(self):
        ProductMattress.objects.create(
            product=self.product,
            name=self.hidden_option.name,
            is_hidden=True,
        )

        response = self.client.get(f"/api/products/{self.product.id}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["name"] for item in response.data["mattresses"]],
            [self.visible_option.name],
        )

    def test_admin_product_detail_includes_hidden_mattresses_for_editing(self):
        ProductMattress.objects.create(
            product=self.product,
            name=self.hidden_option.name,
            is_hidden=True,
        )
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(f"/api/products/{self.product.id}/")

        self.assertEqual(response.status_code, 200)
        mattresses_by_name = {item["name"]: item for item in response.data["mattresses"]}
        self.assertIn(self.hidden_option.name, mattresses_by_name)
        self.assertTrue(mattresses_by_name[self.hidden_option.name]["is_hidden"])
        self.assertIn(self.visible_option.name, mattresses_by_name)
        self.assertFalse(mattresses_by_name[self.visible_option.name]["is_hidden"])

    def test_admin_can_save_hidden_mattress_override(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.patch(
            f"/api/products/{self.product.id}/",
            {
                "mattresses": [
                    {
                        "name": self.hidden_option.name,
                        "is_hidden": True,
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        saved_override = ProductMattress.objects.get(product=self.product, name=self.hidden_option.name)
        self.assertTrue(saved_override.is_hidden)
