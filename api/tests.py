from django.core import mail
from django.test import TestCase, override_settings
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
            "address": "221B Baker Street",
            "city": "London",
            "postal_code": "NW1 6XE",
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
        self.assertIn("ORD-", customer_email.subject)
        self.assertIn("Ayesha Jahangir", customer_email.body)
        self.assertIn("Please call before delivery.", customer_email.body)
        self.assertIn("Buttoned headboard", customer_email.body)
        self.assertIn("Plush Velvet", customer_email.body)
        self.assertEqual(len(customer_email.attachments), 1)
