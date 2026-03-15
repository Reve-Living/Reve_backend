from django.core import mail
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from rest_framework.test import APIClient


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
        self.assertIn("2. EMAIL AUTOMATION - CUSTOMER ORDER CONFIRMATION", customer_email.body)
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
