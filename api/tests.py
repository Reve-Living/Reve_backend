from django.core import mail
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from rest_framework.test import APIClient

from .models import Category, Product


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
        self.assertIn("Email: support@reveliving.co.uk", customer_email.body)
        self.assertIn("Phone: +44 7386 340475", customer_email.body)
        self.assertEqual(len(customer_email.attachments), 1)

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


class ProductSortOrderSwapTests(TestCase):
    def test_product_list_places_positive_sort_order_before_zero(self):
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
        self.assertEqual(response.data[0]["id"], prioritized.id)

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
