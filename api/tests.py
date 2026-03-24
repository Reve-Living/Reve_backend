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
        self.assertEqual(first_product.sort_order, created_product.id)

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
        self.assertEqual(first_product.sort_order, 5)
