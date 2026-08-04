from django.core import mail
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from rest_framework.test import APIClient
from unittest.mock import patch
import tempfile

from .models import (
    Category,
    Order,
    Product,
    Review,
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
    CategoryFilter,
    DimensionTemplate,
    MattressOption,
    ProductDimensionTemplate,
)


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    EMAIL_HOST="smtp.hostinger.com",
    DEFAULT_FROM_EMAIL="info@reveliving.co.uk",
    ORDER_NOTIFICATION_EMAIL="info@reveliving.co.uk",
    MEDIA_ROOT=tempfile.mkdtemp(),
    MEDIA_URL="/media/",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class OrderEmailTests(TestCase):
    def test_cash_on_delivery_order_creation_sends_customer_and_admin_emails(self):
        client = APIClient()
        upload_response = client.post(
            "/api/orders/upload_reference_image/",
            {"file": SimpleUploadedFile("customer-photo.jpg", b"image-bytes", content_type="image/jpeg")},
            format="multipart",
        )
        self.assertEqual(upload_response.status_code, 201)
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
            "payment_method": "cod",
            "payment_id": "public-client-cannot-set-this",
            "status": "paid",
            "send_confirmation_email": False,
            "special_notes": "Please call before delivery.",
            "reference_images": [upload_response.data["url"]],
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
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["payment_id"], "")

        recipients = sorted(message.to[0] for message in mail.outbox)
        self.assertEqual(recipients, ["customer@example.com", "info@reveliving.co.uk"])

        customer_email = next(message for message in mail.outbox if message.to == ["customer@example.com"])
        order_id = response.data["id"]
        self.assertEqual(customer_email.subject, f"Order Confirmation - Reve Living (Order #{order_id})")
        self.assertNotIn("2. EMAIL AUTOMATION - CUSTOMER ORDER CONFIRMATION", customer_email.body)
        self.assertNotIn("Subject: Order Confirmation", customer_email.body)
        self.assertIn("Ayesha Jahangir", customer_email.body)
        self.assertIn("Product Name | Quantity | Price", customer_email.body)
        self.assertIn("Payment Method: Cash on Delivery", customer_email.body)
        self.assertIn("Assembly Service: £49.00", customer_email.body)
        self.assertIn("Email: support@reveliving.co.uk", customer_email.body)
        self.assertIn("Phone: +44 7386 340475", customer_email.body)
        self.assertEqual(response.data["reference_images"], [upload_response.data["url"]])
        self.assertEqual(len(customer_email.attachments), 0)
        self.assertTrue(response.data["confirmation_email_sent_at"])

        order_item = response.data["items"][0]
        self.assertTrue(order_item["assembly_service_selected"])
        self.assertEqual(order_item["assembly_service_price"], "49.00")

    def test_card_order_creation_waits_for_paid_confirmation_before_email(self):
        client = APIClient()
        payload = {
            "first_name": "Card",
            "last_name": "Pending",
            "email": "card-pending@example.com",
            "phone": "+44 1234 567890",
            "address": "1 Payment Street",
            "city": "London",
            "postal_code": "E1 1AA",
            "delivery_charges": "0.00",
            "payment_method": "card",
            "items": [
                {
                    "quantity": 1,
                    "price": "299.99",
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = client.post("/api/orders/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["payment_id"], "")
        self.assertIsNone(response.data["confirmation_email_sent_at"])
        self.assertEqual(len(mail.outbox), 0)

    def test_admin_can_create_manual_order_without_sending_emails(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="manual-orders-admin",
            password="password123",
            email="admin@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        payload = {
            "first_name": "Manual",
            "last_name": "Order",
            "email": "manual@example.com",
            "phone": "+44 1234 567890",
            "address": "10 Downing Street",
            "city": "London",
            "postal_code": "SW1A 2AA",
            "delivery_charges": "25.00",
            "payment_method": "bank_transfer",
            "payment_id": "WHATSAPP-ORDER-001",
            "send_confirmation_email": False,
            "special_notes": "Order source: WhatsApp",
            "items": [
                {
                    "quantity": 1,
                    "price": "499.99",
                    "size": "King",
                    "color": "Stone",
                    "style": "Fabric: Plush Velvet | Headboard: Wingback",
                    "extras_total": "50.00",
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = client.post("/api/orders/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(response.data["payment_id"], "WHATSAPP-ORDER-001")
        self.assertEqual(response.data["payment_method"], "bank_transfer")
        self.assertEqual(response.data["total_amount"], "524.99")

    def test_admin_manual_order_can_skip_customer_details(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="manual-orders-admin-optional",
            password="password123",
            email="admin-optional@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        payload = {
            "first_name": "",
            "last_name": "",
            "email": "",
            "phone": "",
            "address": "",
            "city": "",
            "postal_code": "",
            "delivery_charges": "0.00",
            "payment_method": "paid",
            "payment_id": "Website paid",
            "send_confirmation_email": True,
            "special_notes": "Order source: WhatsApp",
            "items": [
                {
                    "quantity": 1,
                    "price": "299.99",
                    "style": "Fabric: Plush Velvet",
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = client.post("/api/orders/", payload, format="json")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["email"], "")
        self.assertEqual(response.data["payment_method"], "paid")
        self.assertEqual(response.data["payment_id"], "Website paid")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["info@reveliving.co.uk"])

    def test_public_order_still_requires_customer_details(self):
        client = APIClient()
        payload = {
            "first_name": "",
            "last_name": "",
            "email": "",
            "phone": "",
            "address": "",
            "city": "",
            "postal_code": "",
            "delivery_charges": "0.00",
            "payment_method": "paypal",
            "items": [
                {
                    "quantity": 1,
                    "price": "299.99",
                }
            ],
        }

        response = client.post("/api/orders/", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("First name is required", str(response.data["first_name"]))
        self.assertIn("Last name is required", str(response.data["last_name"]))
        self.assertIn("Email is required", str(response.data["email"]))
        self.assertIn("Phone number is required", str(response.data["phone"]))
        self.assertIn("Address is required", str(response.data["address"]))
        self.assertIn("City is required", str(response.data["city"]))
        self.assertIn("Postal code is required", str(response.data["postal_code"]))

    def test_public_lookup_returns_order_for_matching_email(self):
        client = APIClient()
        payload = {
            "first_name": "Lookup",
            "last_name": "Customer",
            "email": "lookup@example.com",
            "phone": "+44 1111 111111",
            "address": "44 Lookup Lane",
            "city": "Leicester",
            "postal_code": "LE1 1AA",
            "delivery_charges": "10.00",
            "payment_method": "card",
            "items": [
                {
                    "quantity": 1,
                    "price": "199.99",
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            create_response = client.post("/api/orders/", payload, format="json")

        lookup_response = client.post(
            "/api/orders/lookup/",
            {"order_id": create_response.data["id"], "email": "lookup@example.com"},
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(lookup_response.status_code, 200)
        self.assertEqual(lookup_response.data["id"], create_response.data["id"])
        self.assertEqual(lookup_response.data["email"], "lookup@example.com")

    def test_public_order_rejects_embedded_reference_image_payloads(self):
        client = APIClient()
        payload = {
            "first_name": "Inline",
            "last_name": "Image",
            "email": "inline-image@example.com",
            "phone": "+44 1010 101010",
            "address": "10 Inline Road",
            "city": "London",
            "postal_code": "E1 1AA",
            "delivery_charges": "0.00",
            "payment_method": "cod",
            "reference_images": [
                "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wn4nVQAAAAASUVORK5CYII="
            ],
            "items": [
                {
                    "quantity": 1,
                    "price": "199.99",
                }
            ],
        }

        response = client.post("/api/orders/", payload, format="json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("Please upload reference images", str(response.data["reference_images"]))

    @patch("api.views.get_stripe_payment_details")
    def test_public_mark_paid_allows_matching_email(self, mock_get_stripe_payment_details):
        mock_get_stripe_payment_details.return_value = {
            "stripe_checkout_session_id": "cs_test_123",
            "stripe_payment_intent_id": "pi_test_123",
            "stripe_payment_status": "paid",
        }
        client = APIClient()
        payload = {
            "first_name": "Payment",
            "last_name": "Customer",
            "email": "payment@example.com",
            "phone": "+44 2222 222222",
            "address": "22 Payment Road",
            "city": "Birmingham",
            "postal_code": "B1 1AA",
            "delivery_charges": "0.00",
            "payment_method": "card",
            "items": [
                {
                    "quantity": 1,
                    "price": "149.99",
                }
            ],
        }

        create_response = client.post("/api/orders/", payload, format="json")
        with self.captureOnCommitCallbacks(execute=True):
            paid_response = client.post(
                f"/api/orders/{create_response.data['id']}/mark_paid/",
                {
                    "email": "payment@example.com",
                    "payment_method": "card",
                    "payment_id": "cs_test_123",
                },
                format="json",
            )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(paid_response.status_code, 200)
        self.assertEqual(paid_response.data["status"], "paid")
        self.assertEqual(paid_response.data["payment_id"], "cs_test_123")
        self.assertEqual(paid_response.data["payment_metadata"]["stripe_payment_intent_id"], "pi_test_123")
        self.assertTrue(paid_response.data["confirmation_email_sent_at"])
        self.assertEqual(len(mail.outbox), 2)

        with self.captureOnCommitCallbacks(execute=True):
            repeat_response = client.post(
                f"/api/orders/{create_response.data['id']}/mark_paid/",
                {
                    "email": "payment@example.com",
                    "payment_method": "card",
                    "payment_id": "cs_test_123",
                },
                format="json",
            )

        self.assertEqual(repeat_response.status_code, 200)
        self.assertEqual(len(mail.outbox), 2)

    @patch("api.views.get_stripe_payment_details")
    def test_public_mark_paid_rejects_unpaid_card_session_without_email(self, mock_get_stripe_payment_details):
        mock_get_stripe_payment_details.return_value = {
            "stripe_checkout_session_id": "cs_test_unpaid",
            "stripe_payment_status": "unpaid",
        }
        client = APIClient()
        payload = {
            "first_name": "Unpaid",
            "last_name": "Card",
            "email": "unpaid-card@example.com",
            "phone": "+44 2222 222222",
            "address": "22 Pending Road",
            "city": "Birmingham",
            "postal_code": "B1 1AA",
            "delivery_charges": "0.00",
            "payment_method": "card",
            "items": [
                {
                    "quantity": 1,
                    "price": "149.99",
                }
            ],
        }

        create_response = client.post("/api/orders/", payload, format="json")
        with self.captureOnCommitCallbacks(execute=True):
            paid_response = client.post(
                f"/api/orders/{create_response.data['id']}/mark_paid/",
                {
                    "email": "unpaid-card@example.com",
                    "payment_method": "card",
                    "payment_id": "cs_test_unpaid",
                },
                format="json",
            )

        self.assertEqual(create_response.status_code, 201)
        self.assertEqual(paid_response.status_code, 400)
        self.assertIn("not paid yet", str(paid_response.data["payment_id"]))
        order = Order.objects.get(pk=create_response.data["id"])
        self.assertEqual(order.status, "pending")
        self.assertIsNone(order.confirmation_email_sent_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_public_cancellation_is_rejected(self):
        client = APIClient()
        payload = {
            "first_name": "Cancel",
            "last_name": "Customer",
            "email": "cancel@example.com",
            "phone": "+44 3333 333333",
            "address": "3 Cancel Street",
            "city": "Liverpool",
            "postal_code": "L1 1AA",
            "delivery_charges": "0.00",
            "payment_method": "cod",
            "items": [
                {
                    "quantity": 1,
                    "price": "249.99",
                }
            ],
        }

        with self.captureOnCommitCallbacks(execute=True):
            create_response = client.post("/api/orders/", payload, format="json")

        mail.outbox.clear()

        cancel_response = client.post(
            f"/api/orders/{create_response.data['id']}/mark_cancelled/",
            format="json",
        )

        self.assertIn(cancel_response.status_code, (401, 403))
        self.assertEqual(len(mail.outbox), 0)

        order = Order.objects.get(pk=create_response.data["id"])
        self.assertEqual(order.status, "pending")
        self.assertIsNone(order.cancelled_at)

    @patch("api.views.refund_stripe_payment")
    def test_admin_cancellation_refunds_prepaid_stripe_order(self, mock_refund_stripe_payment):
        mock_refund_stripe_payment.return_value = {
            "status": "succeeded",
            "provider": "stripe",
            "refund_id": "re_test_123",
            "payment_metadata": {
                "stripe_checkout_session_id": "cs_test_123",
                "stripe_payment_intent_id": "pi_test_123",
                "stripe_payment_status": "paid",
            },
            "error": "",
        }

        client = APIClient()
        admin_user = User.objects.create_user(
            username="cancel-admin",
            password="password123",
            email="cancel-admin@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)
        payload = {
            "first_name": "Refund",
            "last_name": "Stripe",
            "email": "refund-stripe@example.com",
            "phone": "+44 4444 444444",
            "address": "4 Refund Street",
            "city": "London",
            "postal_code": "W1 1AA",
            "delivery_charges": "0.00",
            "payment_method": "card",
            "items": [
                {
                    "quantity": 1,
                    "price": "499.99",
                }
            ],
        }

        create_response = client.post("/api/orders/", payload, format="json")
        order = Order.objects.get(pk=create_response.data["id"])
        order.status = "paid"
        order.payment_method = "card"
        order.payment_id = "cs_test_123"
        order.payment_metadata = {
            "stripe_checkout_session_id": "cs_test_123",
            "stripe_payment_intent_id": "pi_test_123",
            "stripe_payment_status": "paid",
        }
        order.save(update_fields=["status", "payment_method", "payment_id", "payment_metadata"])

        mail.outbox.clear()

        with self.captureOnCommitCallbacks(execute=True):
            cancel_response = client.post(
                f"/api/orders/{order.id}/mark_cancelled/",
                {"email": "refund-stripe@example.com"},
                format="json",
            )

        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.data["refund_status"], "succeeded")
        self.assertEqual(cancel_response.data["refund_provider"], "stripe")
        self.assertEqual(cancel_response.data["refund_id"], "re_test_123")
        self.assertTrue(cancel_response.data["refunded_at"])
        self.assertEqual(len(mail.outbox), 2)

    def test_admin_cancellation_sets_cancelled_at_and_skips_refund_for_cod(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="cancel-cod-admin",
            password="password123",
            email="cancel-cod-admin@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)
        order = Order.objects.create(
            first_name="Cancel",
            last_name="COD",
            email="cancel-cod@example.com",
            phone="+44 3333 333333",
            address="3 Cancel Street",
            city="Liverpool",
            postal_code="L1 1AA",
            total_amount="249.99",
            delivery_charges="0.00",
            payment_method="cod",
            status="pending",
        )

        with self.captureOnCommitCallbacks(execute=True):
            cancel_response = client.post(
                f"/api/orders/{order.id}/mark_cancelled/",
                format="json",
            )

        self.assertEqual(cancel_response.status_code, 200)
        self.assertEqual(cancel_response.data["status"], "cancelled")
        self.assertEqual(cancel_response.data["refund_status"], "not_required")
        self.assertTrue(cancel_response.data["cancelled_at"])
        self.assertEqual(len(mail.outbox), 2)

        customer_email = next(message for message in mail.outbox if message.to == ["cancel-cod@example.com"])
        self.assertEqual(
            customer_email.subject,
            f"Order Cancelled - Reve Living (Order #{order.id})",
        )
        self.assertIn("Cancellation Date:", customer_email.body)

    @patch("api.views.paypal_request")
    @patch("api.views.paypal_access_token")
    def test_paypal_capture_endpoint_marks_order_paid_and_stores_capture_id(self, mock_paypal_access_token, mock_paypal_request):
        mock_paypal_access_token.return_value = ("token-123", None)

        order = Order.objects.create(
            first_name="PayPal",
            last_name="Capture",
            email="paypal@example.com",
            phone="+44 5555 555555",
            address="5 PayPal Road",
            city="Leeds",
            postal_code="LS1 2AB",
            total_amount="100.00",
            delivery_charges="0.00",
            payment_method="paypal",
        )

        class MockResponse:
            def json(self):
                return {
                    "id": "PAYPAL-ORDER-123",
                    "purchase_units": [
                        {
                            "custom_id": str(order.id),
                            "payments": {
                                "captures": [
                                    {
                                        "id": "PAYPAL-CAPTURE-123",
                                        "status": "COMPLETED",
                                    }
                                ]
                            },
                        }
                    ],
                }

        mock_paypal_request.return_value = (MockResponse(), None)

        client = APIClient()
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post("/api/payments/capture_paypal_order/", {"orderID": "PAYPAL-ORDER-123"}, format="json")

        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(order.payment_id, "PAYPAL-CAPTURE-123")
        self.assertEqual(order.payment_metadata["paypal_order_id"], "PAYPAL-ORDER-123")
        self.assertEqual(order.payment_metadata["paypal_capture_id"], "PAYPAL-CAPTURE-123")
        self.assertEqual(order.payment_metadata["paypal_capture_status"], "COMPLETED")
        self.assertIsNotNone(order.confirmation_email_sent_at)
        self.assertEqual(len(mail.outbox), 2)

    @patch("api.views.stripe.checkout.Session.create")
    def test_create_stripe_session_stores_checkout_session_reference(self, mock_stripe_session_create):
        class MockSession:
            id = "cs_test_456"
            url = "https://checkout.stripe.com/c/pay/test"

        mock_stripe_session_create.return_value = MockSession()

        client = APIClient()
        payload = {
            "first_name": "Stripe",
            "last_name": "Session",
            "email": "stripe-session@example.com",
            "phone": "+44 6666 666666",
            "address": "6 Stripe Road",
            "city": "Manchester",
            "postal_code": "M1 2AB",
            "delivery_charges": "0.00",
            "payment_method": "card",
            "items": [
                {
                    "quantity": 1,
                    "price": "199.99",
                }
            ],
        }

        create_response = client.post("/api/orders/", payload, format="json")
        response = client.post(
            "/api/payments/create_stripe_session/",
            {
                "order_id": create_response.data["id"],
                "items": [{"name": "Bed", "price": "199.99", "quantity": 1}],
                "delivery_charges": "0.00",
                "currency": "gbp",
                "success_url": "https://example.com/checkout?success=1",
                "cancel_url": "https://example.com/checkout?canceled=1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stripe_session_create.call_args.kwargs["payment_method_types"], ["card"])
        order = Order.objects.get(pk=create_response.data["id"])
        self.assertEqual(order.payment_id, "cs_test_456")
        self.assertEqual(order.payment_method, "card")
        self.assertEqual(order.payment_metadata["stripe_checkout_session_id"], "cs_test_456")
        self.assertEqual(order.payment_metadata["requested_payment_method"], "card")

    @patch("api.views.stripe.checkout.Session.create")
    def test_create_stripe_session_can_force_klarna(self, mock_stripe_session_create):
        class MockSession:
            id = "cs_test_klarna"
            url = "https://checkout.stripe.com/c/pay/klarna"

        mock_stripe_session_create.return_value = MockSession()

        client = APIClient()
        create_response = client.post(
            "/api/orders/",
            {
                "first_name": "Klarna",
                "last_name": "Customer",
                "email": "klarna@example.com",
                "phone": "+44 6666 666666",
                "address": "6 Stripe Road",
                "city": "Manchester",
                "postal_code": "M1 2AB",
                "delivery_charges": "0.00",
                "payment_method": "klarna",
                "items": [{"quantity": 1, "price": "199.99"}],
            },
            format="json",
        )

        response = client.post(
            "/api/payments/create_stripe_session/",
            {
                "order_id": create_response.data["id"],
                "payment_method": "klarna",
                "items": [{"name": "Bed", "price": "199.99", "quantity": 1}],
                "delivery_charges": "0.00",
                "currency": "gbp",
                "success_url": "https://example.com/checkout?success=1",
                "cancel_url": "https://example.com/checkout?canceled=1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stripe_session_create.call_args.kwargs["payment_method_types"], ["klarna"])
        order = Order.objects.get(pk=create_response.data["id"])
        self.assertEqual(order.payment_method, "klarna")
        self.assertEqual(order.payment_metadata["requested_payment_method"], "klarna")

    @patch("api.views.stripe.checkout.Session.create")
    def test_create_stripe_session_uses_card_rails_for_google_pay(self, mock_stripe_session_create):
        class MockSession:
            id = "cs_test_google_pay"
            url = "https://checkout.stripe.com/c/pay/google-pay"

        mock_stripe_session_create.return_value = MockSession()

        client = APIClient()
        create_response = client.post(
            "/api/orders/",
            {
                "first_name": "Google",
                "last_name": "Pay",
                "email": "google-pay@example.com",
                "phone": "+44 6666 666666",
                "address": "6 Stripe Road",
                "city": "Manchester",
                "postal_code": "M1 2AB",
                "delivery_charges": "0.00",
                "payment_method": "google_pay",
                "items": [{"quantity": 1, "price": "199.99"}],
            },
            format="json",
        )

        response = client.post(
            "/api/payments/create_stripe_session/",
            {
                "order_id": create_response.data["id"],
                "payment_method": "google_pay",
                "items": [{"name": "Bed", "price": "199.99", "quantity": 1}],
                "delivery_charges": "0.00",
                "currency": "gbp",
                "success_url": "https://example.com/checkout?success=1",
                "cancel_url": "https://example.com/checkout?canceled=1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stripe_session_create.call_args.kwargs["payment_method_types"], ["card"])
        order = Order.objects.get(pk=create_response.data["id"])
        self.assertEqual(order.payment_method, "google_pay")
        self.assertEqual(order.payment_metadata["requested_payment_method"], "google_pay")

    @patch("api.views.stripe.checkout.Session.create")
    def test_create_stripe_session_can_force_clearpay(self, mock_stripe_session_create):
        class MockSession:
            id = "cs_test_clearpay"
            url = "https://checkout.stripe.com/c/pay/clearpay"

        mock_stripe_session_create.return_value = MockSession()

        client = APIClient()
        create_response = client.post(
            "/api/orders/",
            {
                "first_name": "Clearpay",
                "last_name": "Customer",
                "email": "clearpay@example.com",
                "phone": "+44 6666 666666",
                "address": "6 Stripe Road",
                "city": "Manchester",
                "postal_code": "M1 2AB",
                "delivery_charges": "0.00",
                "payment_method": "afterpay_clearpay",
                "items": [{"quantity": 1, "price": "199.99"}],
            },
            format="json",
        )

        response = client.post(
            "/api/payments/create_stripe_session/",
            {
                "order_id": create_response.data["id"],
                "payment_method": "afterpay_clearpay",
                "items": [{"name": "Bed", "price": "199.99", "quantity": 1}],
                "delivery_charges": "0.00",
                "currency": "gbp",
                "success_url": "https://example.com/checkout?success=1",
                "cancel_url": "https://example.com/checkout?canceled=1",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_stripe_session_create.call_args.kwargs["payment_method_types"], ["afterpay_clearpay"])
        order = Order.objects.get(pk=create_response.data["id"])
        self.assertEqual(order.payment_method, "afterpay_clearpay")
        self.assertEqual(order.payment_metadata["requested_payment_method"], "afterpay_clearpay")

    def test_admin_can_update_existing_order_and_replace_items(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="manual-orders-admin-edit",
            password="password123",
            email="admin-edit@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        create_payload = {
            "first_name": "Original",
            "last_name": "Customer",
            "email": "original@example.com",
            "phone": "+44 1000 000000",
            "address": "10 Original Street",
            "city": "Leeds",
            "postal_code": "LS1 1AA",
            "delivery_charges": "15.00",
            "payment_method": "paid",
            "payment_id": "OLD-REF",
            "send_confirmation_email": False,
            "special_notes": "Order source: WhatsApp",
            "items": [
                {
                    "quantity": 1,
                    "price": "199.99",
                    "size": "Double",
                }
            ],
        }

        create_response = client.post("/api/orders/", create_payload, format="json")
        self.assertEqual(create_response.status_code, 201)

        update_payload = {
            "first_name": "Updated",
            "last_name": "Customer",
            "email": "",
            "phone": "+44 2000 000000",
            "address": "22 Updated Avenue",
            "city": "Manchester",
            "postal_code": "M1 1AA",
            "delivery_charges": "20.00",
            "payment_method": "cash_on_delivery",
            "payment_id": "NEW-REF",
            "special_notes": "Order source: Phone\nCustomer requested evening delivery.",
            "items": [
                {
                    "quantity": 2,
                    "price": "149.50",
                    "color": "Stone",
                },
                {
                    "quantity": 1,
                    "price": "89.99",
                    "style": "Fabric: Plush Velvet",
                },
            ],
        }

        response = client.put(f"/api/orders/{create_response.data['id']}/", update_payload, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["first_name"], "Updated")
        self.assertEqual(response.data["payment_method"], "cash_on_delivery")
        self.assertEqual(response.data["payment_id"], "NEW-REF")
        self.assertEqual(response.data["delivery_charges"], "20.00")
        self.assertEqual(response.data["total_amount"], "408.99")
        self.assertEqual(len(response.data["items"]), 2)
        self.assertEqual(response.data["items"][0]["quantity"], 2)
        self.assertEqual(response.data["items"][1]["price"], "89.99")

    def test_admin_can_delete_existing_order(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="manual-orders-admin-delete",
            password="password123",
            email="admin-delete@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        create_payload = {
            "first_name": "Delete",
            "last_name": "Me",
            "email": "delete@example.com",
            "phone": "+44 3000 000000",
            "address": "3 Delete Road",
            "city": "Bristol",
            "postal_code": "BS1 1AA",
            "delivery_charges": "0.00",
            "payment_method": "paid",
            "send_confirmation_email": False,
            "items": [
                {
                    "quantity": 1,
                    "price": "99.99",
                }
            ],
        }

        create_response = client.post("/api/orders/", create_payload, format="json")
        self.assertEqual(create_response.status_code, 201)

        delete_response = client.delete(f"/api/orders/{create_response.data['id']}/")
        fetch_response = client.get(f"/api/orders/{create_response.data['id']}/")

        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(fetch_response.status_code, 404)

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


@override_settings(
    MEDIA_ROOT=tempfile.mkdtemp(),
    MEDIA_URL="/media/",
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    },
)
class ReviewMediaTests(TestCase):
    def test_public_review_can_upload_media_and_stays_hidden_until_approved(self):
        category = Category.objects.create(name="Beds", slug="review-media-beds")
        product = Product.objects.create(
            name="Review Media Bed",
            slug="review-media-bed",
            category=category,
            price="299.99",
            description="A bed for review media tests.",
        )
        client = APIClient()

        upload_response = client.post(
            "/api/reviews/upload_media/",
            {"file": SimpleUploadedFile("customer-photo.jpg", b"image-bytes", content_type="image/jpeg")},
            format="multipart",
        )

        self.assertEqual(upload_response.status_code, 201)
        self.assertEqual(upload_response.data["type"], "image")
        self.assertIn("/media/review-media/", upload_response.data["url"])

        create_response = client.post(
            "/api/reviews/",
            {
                "product": product.id,
                "name": "Customer",
                "rating": 5,
                "comment": "Lovely product with photo.",
                "media": [upload_response.data],
            },
            format="json",
        )

        self.assertEqual(create_response.status_code, 201)
        self.assertFalse(create_response.data["is_visible"])
        self.assertEqual(create_response.data["media"][0]["type"], "image")

        external_media_response = client.post(
            "/api/reviews/",
            {
                "product": product.id,
                "name": "Customer",
                "rating": 5,
                "comment": "Trying to attach an external media URL.",
                "media": [{"url": "https://example.com/review-media/customer-photo.jpg", "type": "image"}],
            },
            format="json",
        )
        self.assertEqual(external_media_response.status_code, 400)

        public_response = client.get(f"/api/reviews/?product={product.id}")
        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(public_response.data, [])

        review = Review.objects.get(pk=create_response.data["id"])
        review.is_visible = True
        review.save(update_fields=["is_visible"])

        visible_response = client.get(f"/api/reviews/?product={product.id}")
        self.assertEqual(visible_response.status_code, 200)
        self.assertEqual(visible_response.data[0]["media"][0]["url"], upload_response.data["url"])

    def test_review_visibility_updates_product_rating_and_count(self):
        category = Category.objects.create(name="Tables", slug="review-summary-tables")
        product = Product.objects.create(
            name="Review Summary Table",
            slug="review-summary-table",
            category=category,
            price="499.99",
            description="A table used to verify review aggregation.",
        )

        first_review = Review.objects.create(
            product=product,
            name="Karen",
            rating=5,
            comment="Excellent finish.",
            is_visible=False,
        )
        second_review = Review.objects.create(
            product=product,
            name="Mo",
            rating=3,
            comment="Looks great in person.",
            is_visible=True,
        )

        product.refresh_from_db()
        self.assertEqual(str(product.rating), "3.0")
        self.assertEqual(product.review_count, 1)

        first_review.is_visible = True
        first_review.save(update_fields=["is_visible"])

        product.refresh_from_db()
        self.assertEqual(str(product.rating), "4.0")
        self.assertEqual(product.review_count, 2)

        second_review.delete()

        product.refresh_from_db()
        self.assertEqual(str(product.rating), "5.0")
        self.assertEqual(product.review_count, 1)

    def test_product_list_uses_live_review_summary_for_existing_visible_reviews(self):
        category = Category.objects.create(name="Living Room", slug="living-room-review-list")
        product = Product.objects.create(
            name="Rowley High Gloss Sideboard - 3 Door",
            slug="rowley-high-gloss-sideboard-3-door",
            category=category,
            price="549.00",
            description="Used to verify storefront review summaries.",
            rating="0.0",
            review_count=0,
        )
        Review.objects.create(
            product=product,
            name="Karen",
            rating=5,
            comment="Excellent.",
            is_visible=True,
        )
        Product.objects.filter(pk=product.pk).update(rating="0.0", review_count=0)

        response = APIClient().get(f"/api/products/?category={category.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["rating"], 5.0)
        self.assertEqual(response.data[0]["review_count"], 1)

        summary_response = APIClient().get(f"/api/products/?category={category.slug}&summary=1")

        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(len(summary_response.data), 1)
        self.assertEqual(summary_response.data[0]["rating"], 5.0)
        self.assertEqual(summary_response.data[0]["review_count"], 1)


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

    def test_product_list_includes_shared_subcategory_products_for_linked_category(self):
        beds = Category.objects.create(name="Beds", slug="beds-shared-products", sort_order=1)
        wooden = Category.objects.create(name="Wooden Beds", slug="wooden-beds-shared-products", sort_order=2)
        subcategory = SubCategory.objects.create(name="Classic Frames", slug="classic-frames-shared", category=beds)
        subcategory.additional_categories.add(wooden)
        product = Product.objects.create(
            name="Oak Frame Bed",
            slug="oak-frame-bed-shared",
            category=beds,
            subcategory=subcategory,
            price="799.99",
            short_description="Solid oak frame",
            description="Solid oak frame with storage options.",
            is_hidden=False,
            in_stock=True,
        )

        response = self.client.get(f"/api/products/?category={wooden.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [product.id])

    def test_category_filters_count_shared_subcategory_products_for_linked_category(self):
        beds = Category.objects.create(name="Beds", slug="beds-shared-filter-counts", sort_order=1)
        wooden = Category.objects.create(name="Wooden Beds", slug="wooden-beds-shared-filter-counts", sort_order=2)
        subcategory = SubCategory.objects.create(name="Classic Frames", slug="classic-frames-filter-shared", category=beds)
        subcategory.additional_categories.add(wooden)
        product = Product.objects.create(
            name="Walnut Storage Bed",
            slug="walnut-storage-bed-shared",
            category=beds,
            subcategory=subcategory,
            price="899.99",
            short_description="Walnut storage bed",
            description="Walnut finish bed with drawer storage.",
            is_hidden=False,
            in_stock=True,
        )
        filter_type = FilterType.objects.create(
            name="Finish",
            slug="finish-shared-count",
            display_type="checkbox",
        )
        option = FilterOption.objects.create(
            filter_type=filter_type,
            name="Walnut",
            slug="walnut-shared-count",
        )
        CategoryFilter.objects.create(subcategory=subcategory, filter_type=filter_type, is_active=True)
        ProductFilterValue.objects.create(product=product, filter_option=option)

        response = APIClient().get(f"/api/categories/{wooden.slug}/filters/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["filters"][0]["slug"], filter_type.slug)
        self.assertEqual(response.data["filters"][0]["options"][0]["product_count"], 1)


class ProductSparseUpdateTests(TestCase):
    def test_sparse_product_put_keeps_existing_related_records(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-sparse-update",
            password="password123",
            email="admin-sparse-update@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-sparse-update", sort_order=1)
        first_subcategory = SubCategory.objects.create(
            name="Wooden Beds",
            slug="wooden-beds-sparse-update",
            category=category,
        )
        second_subcategory = SubCategory.objects.create(
            name="Storage Beds",
            slug="storage-beds-sparse-update",
            category=category,
        )
        filter_type = FilterType.objects.create(
            name="Finish",
            slug="finish-sparse-update",
            display_type="checkbox",
        )
        option = FilterOption.objects.create(
            filter_type=filter_type,
            name="Oak",
            slug="oak-sparse-update",
        )

        product = Product.objects.create(
            name="Sparse Update Bed",
            slug="sparse-update-bed",
            category=category,
            subcategory=first_subcategory,
            price="599.99",
            short_description="Original short description",
            description="Original long description",
            in_stock=True,
        )
        ProductImage.objects.create(product=product, url="https://example.com/bed.webp", alt_text="Main image")
        ProductSize.objects.create(product=product, name="5ft King", description="King", price_delta="0.00")
        ProductFilterValue.objects.create(product=product, filter_option=option)

        response = client.put(
            f"/api/products/{product.id}/",
            {"subcategory": second_subcategory.id},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(product.subcategory_id, second_subcategory.id)
        self.assertEqual(product.images.count(), 1)
        self.assertEqual(product.sizes.count(), 1)
        self.assertEqual(product.filter_values.count(), 1)

    def test_sparse_product_put_allows_linked_shared_subcategory_category_change(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-shared-subcategory-update",
            password="password123",
            email="admin-shared-subcategory-update@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        console_tables = Category.objects.create(name="Console Tables", slug="console-tables-shared-update", sort_order=1)
        side_console_tables = Category.objects.create(
            name="Side & Console Tables",
            slug="side-console-tables-shared-update",
            sort_order=2,
        )
        shared_subcategory = SubCategory.objects.create(
            name="Console Tables",
            slug="console-tables-shared-subcategory-update",
            category=console_tables,
        )
        shared_subcategory.additional_categories.add(side_console_tables)

        existing_target_product = Product.objects.create(
            name="Side Console Table Existing",
            slug="side-console-table-existing",
            category=side_console_tables,
            subcategory=shared_subcategory,
            price="449.99",
            short_description="Existing side console table",
            description="Existing side console table description",
            sort_order=1,
            in_stock=True,
        )
        moving_product = Product.objects.create(
            name="Console Table Moving",
            slug="console-table-moving",
            category=console_tables,
            subcategory=shared_subcategory,
            price="499.99",
            short_description="Moving console table",
            description="Moving console table description",
            sort_order=1,
            in_stock=True,
        )

        response = client.put(
            f"/api/products/{moving_product.id}/",
            {
                "category": side_console_tables.id,
                "subcategory": shared_subcategory.id,
                "sort_order": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        moving_product.refresh_from_db()
        existing_target_product.refresh_from_db()
        self.assertEqual(moving_product.category_id, side_console_tables.id)
        self.assertEqual(moving_product.subcategory_id, shared_subcategory.id)

        ordered_ids = list(
            Product.objects.filter(category=side_console_tables, subcategory=shared_subcategory)
            .order_by("sort_order", "-created_at", "-id")
            .values_list("id", flat=True)
        )
        self.assertEqual(ordered_ids, [moving_product.id, existing_target_product.id])
        self.assertEqual(existing_target_product.sort_order, 2)

    def test_sparse_product_put_rejects_category_not_linked_to_existing_subcategory(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-invalid-shared-subcategory-update",
            password="password123",
            email="admin-invalid-shared-subcategory-update@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        console_tables = Category.objects.create(name="Console Tables", slug="console-tables-invalid-update", sort_order=1)
        sideboards = Category.objects.create(name="Sideboards", slug="sideboards-invalid-update", sort_order=2)
        console_subcategory = SubCategory.objects.create(
            name="Console Tables",
            slug="console-tables-invalid-subcategory-update",
            category=console_tables,
        )
        product = Product.objects.create(
            name="Console Table Product",
            slug="console-table-product-invalid-update",
            category=console_tables,
            subcategory=console_subcategory,
            price="549.99",
            short_description="Console table",
            description="Console table description",
            in_stock=True,
        )

        response = client.put(
            f"/api/products/{product.id}/",
            {"category": sideboards.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("subcategory", response.data)
        product.refresh_from_db()
        self.assertEqual(product.category_id, console_tables.id)
        self.assertEqual(product.subcategory_id, console_subcategory.id)


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
            sofa_feature_highlights=["USB", "Cup Holders"],
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
        self.assertEqual(duplicated.sofa_feature_highlights, ["USB", "Cup Holders"])
        self.assertNotEqual(duplicated.sizes.first().id, size.id)
        self.assertEqual(duplicated.styles.first().size_id, duplicated.sizes.first().id)
        self.assertFalse(duplicated.colors.first().is_available)
        self.assertFalse(duplicated.fabrics.first().colors[0]["is_available"])

    def test_admin_can_import_product_copy_into_subcategory_independently(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-import-copy",
            password="password123",
            email="admin-import-copy@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        source_category = Category.objects.create(name="Beds", slug="beds-import", sort_order=1)
        target_category = Category.objects.create(name="Storage", slug="storage-import", sort_order=2)
        target_subcategory = SubCategory.objects.create(
            category=target_category,
            name="Ottoman Beds",
            slug="ottoman-beds-import",
            sort_order=1,
        )
        source_product = Product.objects.create(
            name="Luna Bed",
            slug="luna-bed-import",
            category=source_category,
            price="799.99",
            short_description="Luna short description",
            description="Luna long description",
            in_stock=True,
            is_hidden=False,
            sort_order=4,
        )
        ProductImage.objects.create(product=source_product, url="https://example.com/luna.jpg", alt_text="Luna")
        ProductSize.objects.create(product=source_product, name="King", description="5ft", price_delta="25.00")

        response = client.post(
            f"/api/products/{source_product.id}/import-copy/",
            {"category": target_category.id, "subcategory": target_subcategory.id},
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        imported_id = response.data["id"]
        self.assertNotEqual(imported_id, source_product.id)

        imported_product = Product.objects.get(pk=imported_id)
        self.assertEqual(imported_product.name, source_product.name)
        self.assertNotEqual(imported_product.slug, source_product.slug)
        self.assertEqual(imported_product.category_id, target_category.id)
        self.assertEqual(imported_product.subcategory_id, target_subcategory.id)
        self.assertEqual(imported_product.imported_from_product_id, source_product.id)
        self.assertEqual(imported_product.images.count(), 1)
        self.assertEqual(imported_product.sizes.count(), 1)
        self.assertGreater(imported_product.sort_order, 0)

        update_response = client.patch(
            f"/api/products/{imported_product.id}/",
            {"name": "Luna Bed Imported", "sort_order": 7},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)

        source_product.refresh_from_db()
        imported_product.refresh_from_db()
        self.assertEqual(source_product.name, "Luna Bed")
        self.assertEqual(source_product.sort_order, 4)
        self.assertEqual(imported_product.name, "Luna Bed Imported")
        self.assertEqual(imported_product.sort_order, 1)

        delete_response = client.delete(f"/api/products/{source_product.id}/")
        self.assertEqual(delete_response.status_code, 204)
        self.assertTrue(Product.objects.filter(pk=imported_product.id).exists())

    def test_import_copy_reuses_existing_import_for_same_target_scope(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-import-reuse",
            password="password123",
            email="admin-import-reuse@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        source_category = Category.objects.create(name="Beds", slug="beds-import-reuse", sort_order=1)
        target_category = Category.objects.create(name="Guest Beds", slug="guest-beds-import-reuse", sort_order=2)
        target_subcategory = SubCategory.objects.create(
            category=target_category,
            name="Day Beds",
            slug="day-beds-import-reuse",
            sort_order=1,
        )
        source_product = Product.objects.create(
            name="Mila Bed",
            slug="mila-bed-import-reuse",
            category=source_category,
            price="499.99",
            short_description="Mila short description",
            description="Mila long description",
            in_stock=True,
            is_hidden=False,
        )

        first_response = client.post(
            f"/api/products/{source_product.id}/import-copy/",
            {"category": target_category.id, "subcategory": target_subcategory.id},
            format="json",
        )
        second_response = client.post(
            f"/api/products/{source_product.id}/import-copy/",
            {"category": target_category.id, "subcategory": target_subcategory.id},
            format="json",
        )

        self.assertEqual(first_response.status_code, 201)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(first_response.data["id"], second_response.data["id"])
        self.assertEqual(
            Product.objects.filter(
                imported_from_product_id=source_product.id,
                category_id=target_category.id,
                subcategory_id=target_subcategory.id,
            ).count(),
            1,
        )


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
                "sofa_feature_highlights": ["USB Charging", "Manual Recliner"],
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
        self.assertEqual(response.data["sofa_feature_highlights"], ["USB Charging", "Manual Recliner"])
        self.assertEqual(response.data["colors"][0]["is_available"], False)
        self.assertEqual(response.data["fabrics"][0]["colors"][0]["is_available"], False)
        self.assertEqual(response.data["fabrics"][0]["colors"][1]["is_available"], True)

        product = Product.objects.get(pk=response.data["id"])
        self.assertEqual(product.sofa_feature_highlights, ["USB Charging", "Manual Recliner"])
        self.assertFalse(product.colors.first().is_available)
        self.assertFalse(product.fabrics.first().colors[0]["is_available"])


class ProductStockStatusTests(TestCase):
    def test_create_product_with_stock_check_needed_sets_non_purchasable_stock(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-stock-status",
            password="password123",
            email="admin-stock-status@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-stock-status", sort_order=1)

        response = client.post(
            "/api/products/",
            {
                "name": "Stock Check Bed",
                "slug": "stock-check-bed",
                "category": category.id,
                "price": "899.99",
                "description": "Stock check description",
                "short_description": "Stock check short description",
                "stock_status": "stock_check_needed",
                "images": [],
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["stock_status"], Product.STOCK_STATUS_STOCK_CHECK_NEEDED)
        self.assertFalse(response.data["in_stock"])

        product = Product.objects.get(pk=response.data["id"])
        self.assertEqual(product.stock_status, Product.STOCK_STATUS_STOCK_CHECK_NEEDED)
        self.assertFalse(product.in_stock)

    def test_update_product_to_low_stock_keeps_it_purchasable(self):
        client = APIClient()
        admin_user = User.objects.create_user(
            username="admin-low-stock-status",
            password="password123",
            email="admin-low-stock-status@example.com",
            is_staff=True,
        )
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-low-stock-status", sort_order=1)
        product = Product.objects.create(
            name="Status Bed",
            slug="status-bed",
            category=category,
            price="699.99",
            short_description="Status short description",
            description="Status long description",
        )

        response = client.patch(
            f"/api/products/{product.id}/",
            {"stock_status": "low_stock"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["stock_status"], Product.STOCK_STATUS_LOW_STOCK)
        self.assertTrue(response.data["in_stock"])

        product.refresh_from_db()
        self.assertEqual(product.stock_status, Product.STOCK_STATUS_LOW_STOCK)
        self.assertTrue(product.in_stock)


class ProductImageOrderTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_product_update_preserves_image_sort_order(self):
        admin_user = User.objects.create_user(
            username="admin-image-order",
            password="password123",
            email="admin-image-order@example.com",
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(user=admin_user)

        category = Category.objects.create(name="Beds", slug="beds-image-sort-save", sort_order=1)
        product = Product.objects.create(
            name="Sortable Image Bed",
            slug="sortable-image-bed",
            category=category,
            price="499.99",
            short_description="Short",
            description="Long",
        )

        response = client.patch(
            f"/api/products/{product.id}/",
            {
                "images": [
                    {"url": "https://example.com/second.jpg", "sort_order": 2},
                    {"url": "https://example.com/first.jpg", "sort_order": 1},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        product.refresh_from_db()
        self.assertEqual(
            list(product.images.order_by("sort_order", "id").values_list("url", flat=True)),
            [
                "https://example.com/first.jpg",
                "https://example.com/second.jpg",
            ],
        )
        self.assertEqual(
            list(product.images.order_by("sort_order", "id").values_list("flip_horizontal", flat=True)),
            [False, False],
        )

    def test_product_image_flip_horizontal_is_serialized_without_changing_image(self):
        category = Category.objects.create(name="Sofas", slug="sofas-image-flip", sort_order=1)
        product = Product.objects.create(
            name="Flip Sofa",
            slug="flip-sofa",
            category=category,
            price="499.99",
            short_description="Short",
            description="Long",
        )
        ProductImage.objects.create(
            product=product,
            url="https://example.com/sofa.jpg",
            alt_text="Sofa facing left",
            flip_horizontal=True,
            sort_order=1,
        )

        detail_response = APIClient().get(f"/api/products/?slug={product.slug}")
        summary_response = APIClient().get(f"/api/products/?category={category.slug}&summary=1&limit=1")

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(detail_response.data[0]["images"][0]["url"], "https://example.com/sofa.jpg")
        self.assertTrue(detail_response.data[0]["images"][0]["flip_horizontal"])
        self.assertTrue(summary_response.data[0]["images"][0]["flip_horizontal"])

    def test_product_summary_handles_products_without_images(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-no-images", sort_order=1)
        product = Product.objects.create(
            name="Image Free Bed",
            slug="image-free-bed",
            category=category,
            price="499.99",
            short_description="Short",
            description="Long",
        )

        response = APIClient().get("/api/products/?summary=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["id"] for item in response.data], [product.id])
        self.assertEqual(response.data[0]["images"], [])

    def test_product_summary_omits_filter_and_variant_fields_by_default(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-filters", sort_order=1)
        product = Product.objects.create(
            name="Filter Ready Bed",
            slug="filter-ready-bed",
            category=category,
            price="599.99",
            short_description="Short",
            description="Long",
        )
        ProductColor.objects.create(
            product=product,
            name="Ivory",
            hex_code="#fffff0",
            is_available=True,
        )
        ProductStyle.objects.create(
            product=product,
            name="Headboard",
            options=[{"label": "Panel"}],
        )
        filter_type = FilterType.objects.create(name="Fabric", slug="fabric")
        filter_option = FilterOption.objects.create(
            filter_type=filter_type,
            name="Boucle",
            slug="boucle",
        )
        ProductFilterValue.objects.create(product=product, filter_option=filter_option)

        response = APIClient().get("/api/products/?summary=1")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("colors", response.data[0])
        self.assertNotIn("styles", response.data[0])
        self.assertNotIn("filter_values", response.data[0])
        self.assertNotIn("sizes", response.data[0])

    def test_product_summary_exposes_size_summary_without_full_sizes_by_default(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-size-summary", sort_order=1)
        product = Product.objects.create(
            name="Size Summary Bed",
            slug="size-summary-bed",
            category=category,
            price="599.99",
            short_description="Short",
            description="Long",
        )
        ProductSize.objects.create(product=product, name="3ft Single", price_delta="399.99")
        ProductSize.objects.create(product=product, name="5ft King", price_delta="699.99")

        response = APIClient().get("/api/products/?summary=1")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sizes", response.data[0])
        self.assertEqual(response.data[0]["min_size_price"], "399.99")
        self.assertEqual(response.data[0]["size_count"], 2)

    def test_product_summary_can_include_sizes_on_demand(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-include-sizes", sort_order=1)
        product = Product.objects.create(
            name="Size Filter Bed",
            slug="size-filter-bed",
            category=category,
            price="599.99",
            short_description="Short",
            description="Long",
        )
        size = ProductSize.objects.create(product=product, name="3ft Single", price_delta="399.99")

        response = APIClient().get("/api/products/?summary=1&include_sizes=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["sizes"][0]["id"], size.id)

    def test_product_summary_limit_returns_only_requested_count(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-limit", sort_order=1)
        for index in range(3):
            Product.objects.create(
                name=f"Limited Bed {index}",
                slug=f"limited-bed-{index}",
                category=category,
                price="599.99",
                short_description="Short",
                description="Long",
                sort_order=index,
            )

        response = APIClient().get(f"/api/products/?category={category.slug}&summary=1&limit=2")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)

    def test_product_summary_limit_supports_offset(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-offset", sort_order=1)
        for index in range(4):
            Product.objects.create(
                name=f"Offset Bed {index}",
                slug=f"offset-bed-{index}",
                category=category,
                price="599.99",
                short_description="Short",
                description="Long",
                sort_order=index,
            )

        response = APIClient().get(f"/api/products/?category={category.slug}&summary=1&limit=2&offset=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["slug"] for item in response.data], ["offset-bed-1", "offset-bed-2"])

    def test_product_summary_can_return_total_with_first_batch(self):
        category = Category.objects.create(name="Dining", slug="dining-summary-total", sort_order=1)
        for index in range(5):
            Product.objects.create(
                name=f"Dining Product {index}",
                slug=f"dining-summary-total-{index}",
                category=category,
                price="299.99",
                description="Long content",
                sort_order=index,
            )

        response = APIClient().get(
            f"/api/products/?category={category.slug}&summary=1&limit=2&include_total=1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 5)
        self.assertEqual(len(response.data["results"]), 2)

    def test_product_summary_omits_heavy_content_unless_requested(self):
        category = Category.objects.create(name="Chairs", slug="chairs-summary-content", sort_order=1)
        Product.objects.create(
            name="Content Chair",
            slug="content-chair-summary",
            category=category,
            price="299.99",
            description="Detailed chair content",
            features=["Solid wood"],
        )

        light_response = APIClient().get(f"/api/products/?category={category.slug}&summary=1&limit=1")
        content_response = APIClient().get(
            f"/api/products/?category={category.slug}&summary=1&limit=1&include_content=1"
        )

        self.assertNotIn("description", light_response.data[0])
        self.assertEqual(content_response.data[0]["description"], "Detailed chair content")

    def test_product_core_detail_omits_expensive_optional_fields(self):
        category = Category.objects.create(name="Beds", slug="beds-core-detail", sort_order=1)
        product = Product.objects.create(
            name="Core Detail Bed",
            slug="core-detail-bed",
            category=category,
            price="599.99",
            short_description="Short",
            description="Long",
        )

        response = APIClient().get(f"/api/products/{product.id}/?core=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], product.id)
        self.assertIn("description", response.data)
        self.assertNotIn("mattresses", response.data)
        self.assertNotIn("filters", response.data)
        self.assertNotIn("videos", response.data)
        self.assertNotIn("suggested_products", response.data)
        self.assertNotIn("suggested_products_data", response.data)

    def test_product_quick_detail_contains_content_without_variant_relations(self):
        category = Category.objects.create(name="Beds", slug="beds-quick-detail", sort_order=1)
        product = Product.objects.create(
            name="Quick Detail Bed",
            slug="quick-detail-bed",
            category=category,
            price="599.99",
            short_description="Short",
            description="Long description",
            features=["Fast detail"],
        )
        ProductImage.objects.create(product=product, url="https://example.com/quick.jpg")
        ProductSize.objects.create(product=product, name="5ft King", price_delta="699.99")

        response = APIClient().get(f"/api/products/{product.id}/?quick=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["description"], "Long description")
        self.assertEqual(response.data["features"], ["Fast detail"])
        self.assertEqual(response.data["images"][0]["url"], "https://example.com/quick.jpg")
        self.assertNotIn("sizes", response.data)
        self.assertNotIn("mattresses", response.data)

    def test_product_detail_cache_does_not_mix_quick_and_full_responses(self):
        category = Category.objects.create(name="Beds", slug="beds-detail-cache-kind", sort_order=1)
        product = Product.objects.create(
            name="Cache Kind Bed",
            slug="cache-kind-bed",
            category=category,
            price="599.99",
            description="Long",
        )
        ProductSize.objects.create(product=product, name="5ft King", price_delta="699.99")
        client = APIClient()

        quick_response = client.get(f"/api/products/{product.id}/?quick=1")
        full_response = client.get(f"/api/products/{product.id}/")

        self.assertNotIn("sizes", quick_response.data)
        self.assertIn("sizes", full_response.data)

    def test_product_summary_can_include_filter_values_on_demand(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-include-filters", sort_order=1)
        product = Product.objects.create(
            name="Filter Ready Bed",
            slug="filter-ready-bed-include-filters",
            category=category,
            price="599.99",
            short_description="Short",
            description="Long",
        )
        filter_type = FilterType.objects.create(name="Fabric", slug="fabric-include")
        filter_option = FilterOption.objects.create(
            filter_type=filter_type,
            name="Boucle",
            slug="boucle-include",
        )
        ProductFilterValue.objects.create(product=product, filter_option=filter_option)

        response = APIClient().get("/api/products/?summary=1&include_filters=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data[0]["filter_values"],
            [{"filter_type": "fabric-include", "option": "boucle-include", "filter_option_id": filter_option.id}],
        )
        self.assertNotIn("colors", response.data[0])
        self.assertNotIn("styles", response.data[0])

    def test_product_summary_can_include_variants_on_demand(self):
        category = Category.objects.create(name="Beds", slug="beds-summary-include-variants", sort_order=1)
        product = Product.objects.create(
            name="Variant Ready Bed",
            slug="variant-ready-bed",
            category=category,
            price="599.99",
            short_description="Short",
            description="Long",
        )
        color = ProductColor.objects.create(
            product=product,
            name="Ivory",
            hex_code="#fffff0",
            is_available=True,
        )
        style = ProductStyle.objects.create(
            product=product,
            name="Headboard",
            options=[{"label": "Panel"}],
        )

        response = APIClient().get("/api/products/?summary=1&include_variants=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data[0]["colors"],
            [{"id": color.id, "name": "Ivory", "hex_code": "#fffff0", "is_available": True}],
        )
        self.assertEqual(
            response.data[0]["styles"],
            [{"id": style.id, "name": "Headboard", "options": [{"label": "Panel"}]}],
        )
        self.assertNotIn("filter_values", response.data[0])

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

    def test_admin_can_save_and_reload_selected_suggested_products(self):
        admin = User.objects.create_user(username="suggestion-admin", password="test", is_staff=True)
        category = Category.objects.create(name="Sofas", slug="sofas-suggestion-admin")
        product = Product.objects.create(name="Main Sofa", slug="main-sofa", category=category, price="499.99")
        first = Product.objects.create(name="First Suggestion", slug="first-suggestion", category=category, price="399.99")
        second = Product.objects.create(name="Second Suggestion", slug="second-suggestion", category=category, price="299.99")
        client = APIClient()
        client.force_authenticate(user=admin)

        update_response = client.patch(
            f"/api/products/{product.id}/",
            {"suggested_products": [first.id, second.id]},
            format="json",
        )
        self.assertEqual(update_response.status_code, 200)

        detail_response = client.get(f"/api/products/{product.id}/?admin_detail=1")
        self.assertEqual(detail_response.status_code, 200)
        self.assertCountEqual(detail_response.data["suggested_products"], [first.id, second.id])


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "test-product-list-cache",
            "TIMEOUT": None,
        }
    }
)
class ProductListCachingTests(TestCase):
    def test_public_product_list_uses_cache_when_backend_is_available(self):
        category = Category.objects.create(name="Beds", slug="beds-no-shared-cache", sort_order=1)
        Product.objects.create(
            name="Cache Fresh Bed",
            slug="cache-fresh-bed",
            category=category,
            price="499.99",
            short_description="Short",
            description="Long",
        )

        with patch("api.views.cache.get", return_value=None) as mock_cache_get, patch("api.views.cache.set") as mock_cache_set:
            response = APIClient().get("/api/products/?summary=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        mock_cache_get.assert_called_once()
        mock_cache_set.assert_called_once()

    def test_subcategory_discount_overrides_category_and_product_discounts(self):
        category = Category.objects.create(
            name="Mattresses",
            slug="mattresses-category-discount",
            discount_override_enabled=True,
            discount_percentage=20,
        )
        subcategory = SubCategory.objects.create(
            category=category,
            name="Memory Foam",
            slug="memory-foam-category-discount",
            discount_override_enabled=True,
            discount_percentage=30,
        )
        product = Product.objects.create(
            name="Category Discount Mattress",
            slug="category-discount-mattress",
            category=category,
            subcategory=subcategory,
            price="169.00",
            original_price="219.00",
            discount_percentage=15,
        )

        response = APIClient().get(f"/api/products/?slug={product.slug}&summary=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["discount_percentage"], 30)
        self.assertEqual(response.data[0]["effective_discount_percentage"], 30)

    def test_subcategory_discount_overrides_product_discount(self):
        category = Category.objects.create(
            name="Beds",
            slug="beds-subcategory-discount",
        )
        subcategory = SubCategory.objects.create(
            category=category,
            name="Divan Beds",
            slug="divan-beds-subcategory-discount",
            discount_override_enabled=True,
            discount_percentage=30,
        )
        product = Product.objects.create(
            name="Subcategory Discount Bed",
            slug="subcategory-discount-bed",
            category=category,
            subcategory=subcategory,
            price="169.00",
            original_price="219.00",
            discount_percentage=15,
        )

        response = APIClient().get(f"/api/products/?slug={product.slug}&summary=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["discount_percentage"], 30)
        self.assertEqual(response.data[0]["effective_discount_percentage"], 30)
        self.assertTrue(response.data[0]["discount_override_applied"])

    def test_admin_product_update_can_return_no_content(self):
        admin_user = User.objects.create_user(
            username="admin-no-content-update",
            password="password123",
            email="admin-no-content-update@example.com",
            is_staff=True,
        )
        category = Category.objects.create(name="Beds", slug="beds-no-content-update", sort_order=1)
        product = Product.objects.create(
            name="No Content Bed",
            slug="no-content-bed",
            category=category,
            price="499.99",
            short_description="Short",
            description="Long",
            sort_order=3,
        )

        client = APIClient()
        client.force_authenticate(user=admin_user)
        response = client.put(
            f"/api/products/{product.id}/?response=none",
            {
                "name": product.name,
                "slug": product.slug,
                "category": category.id,
                "price": "499.99",
                "short_description": product.short_description,
                "description": product.description,
                "sort_order": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 204)
        product.refresh_from_db()
        self.assertEqual(product.sort_order, 1)


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


class MattressOptionScopedProductTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.category = Category.objects.create(name="Kids Beds", slug="kids-beds-mattress-scope", sort_order=1)
        self.product_one = Product.objects.create(
            name="Vision White Bunk Bed",
            slug="vision-white-bunk-bed-scope",
            category=self.category,
            price="499.99",
            short_description="Kids bed",
            description="Kids bed description",
            in_stock=True,
            is_hidden=False,
        )
        self.product_two = Product.objects.create(
            name="Willow Treehouse Bunk Bed",
            slug="willow-treehouse-bunk-bed-scope",
            category=self.category,
            price="599.99",
            short_description="Kids bed",
            description="Another kids bed description",
            in_stock=True,
            is_hidden=False,
        )
        self.category_wide_option = MattressOption.objects.create(
            name="Kids Comfort Mattress",
            display_name="Mattress Number 2",
            description="For all kids beds",
            price="89.99",
        )
        self.category_wide_option.categories.add(self.category)
        self.product_specific_option = MattressOption.objects.create(
            name="Kids Pocket Mattress",
            display_name="Mattress Number 1",
            kids_button_label="Top Mattress",
            description="Only for one kids bed",
            price="129.99",
        )
        self.product_specific_option.categories.add(self.category)
        self.product_specific_option.products.add(self.product_one)

    def test_product_detail_includes_only_matching_product_scoped_mattresses(self):
        response_one = self.client.get(f"/api/products/{self.product_one.id}/")
        response_two = self.client.get(f"/api/products/{self.product_two.id}/")

        self.assertEqual(response_one.status_code, 200)
        self.assertEqual(response_two.status_code, 200)

        names_one = [item["name"] for item in response_one.data["mattresses"]]
        names_two = [item["name"] for item in response_two.data["mattresses"]]

        self.assertCountEqual(names_one, [self.product_specific_option.name, self.category_wide_option.name])
        self.assertEqual(names_two, [self.category_wide_option.name])

    def test_product_detail_exposes_display_name_for_storefront_cards(self):
        response = self.client.get(f"/api/products/{self.product_one.id}/")

        self.assertEqual(response.status_code, 200)
        mattress_lookup = {item["name"]: item for item in response.data["mattresses"]}
        self.assertEqual(
            mattress_lookup[self.product_specific_option.name]["display_name"],
            self.product_specific_option.display_name,
        )
        self.assertEqual(
            mattress_lookup[self.product_specific_option.name]["kids_button_label"],
            self.product_specific_option.kids_button_label,
        )
        self.assertEqual(
            mattress_lookup[self.category_wide_option.name]["display_name"],
            self.category_wide_option.display_name,
        )


class GoogleMerchantFeedTests(TestCase):
    def _feed_xml(self):
        response = APIClient().get("/google-feed.xml")
        self.assertEqual(response.status_code, 200)
        return b"".join(response.streaming_content).decode("utf-8")

    def test_bed_subcategory_feed_uses_lowest_product_price_once(self):
        category = Category.objects.create(name="Beds", slug="beds-feed")
        subcategory = SubCategory.objects.create(name="Divan Beds", slug="divan-beds-feed", category=category)
        product = Product.objects.create(
            name="Lowest Price Divan",
            slug="lowest-price-divan",
            category=category,
            subcategory=subcategory,
            price="399.99",
            short_description="Divan bed",
            description="Divan bed description",
        )
        ProductSize.objects.create(product=product, name="Double", description="4ft6", price_delta="0.00")
        ProductSize.objects.create(product=product, name="Super King", description="6ft", price_delta="600.00")

        xml = self._feed_xml()

        self.assertIn("<g:title>Lowest Price Divan</g:title>", xml)
        self.assertIn("<g:price>399.99 GBP</g:price>", xml)
        self.assertNotIn("999.99 GBP", xml)
        self.assertNotIn("<g:size>", xml)

    def test_non_target_category_feed_keeps_size_variant_prices(self):
        category = Category.objects.create(name="Chairs", slug="chairs-feed")
        product = Product.objects.create(
            name="Variant Chair",
            slug="variant-chair",
            category=category,
            price="199.99",
            short_description="Chair",
            description="Chair description",
        )
        ProductSize.objects.create(product=product, name="Standard", description="", price_delta="199.99")
        ProductSize.objects.create(product=product, name="Large", description="", price_delta="249.99")

        xml = self._feed_xml()

        self.assertIn("<g:title>Variant Chair - Standard</g:title>", xml)
        self.assertIn("<g:title>Variant Chair - Large</g:title>", xml)
        self.assertIn("<g:price>199.99 GBP</g:price>", xml)
        self.assertIn("<g:price>249.99 GBP</g:price>", xml)

    def test_mattress_feed_uses_base_title_and_lowest_size_price_once(self):
        category = Category.objects.create(name="Mattresses", slug="mattresses-feed")
        product = Product.objects.create(
            name="Milano Super Orthopaedic Mattress",
            slug="milano-super-orthopaedic-mattress-feed",
            category=category,
            price="210.00",
            short_description="Firm mattress",
            description="Firm mattress description",
        )
        ProductSize.objects.create(product=product, name="3ft Single", description="", price_delta="169.00")
        ProductSize.objects.create(product=product, name="4ft6 Double", description="", price_delta="338.00")

        xml = self._feed_xml()

        self.assertIn("<g:id>{}</g:id>".format(product.id), xml)
        self.assertIn("<g:title>Milano Super Orthopaedic Mattress</g:title>", xml)
        self.assertIn("<g:price>169.00 GBP</g:price>", xml)
        self.assertNotIn("Milano Super Orthopaedic Mattress - 3ft Single", xml)
        self.assertNotIn("<g:price>338.00 GBP</g:price>", xml)

    def test_feed_adds_structured_product_details_from_existing_product_data(self):
        category = Category.objects.create(name="Sofas", slug="sofas-feed-details")
        subcategory = SubCategory.objects.create(name="Reclining Sofas", slug="reclining-sofas-feed-details", category=category)
        product = Product.objects.create(
            name="Turin 2 Seater Dark Grey Fabric Recliner Sofa",
            slug="turin-2-seater-dark-grey-fabric-recliner-sofa-feed",
            category=category,
            subcategory=subcategory,
            price="499.00",
            short_description="The Turin Dark Grey Fabric recliner sofa.",
            description="The Turin Dark Grey Fabric recliner sofa description.",
            dimensions=[
                {"measurement": "Depth", "values": {"Overall": "92 cm"}},
                {"measurement": "Seat Height", "values": {"Overall": "48 cm"}},
                {"measurement": "Length", "values": {"Overall": "152 cm"}},
            ],
        )
        ProductColor.objects.create(product=product, name="Dark Grey", hex_code="#444444")
        ProductFabric.objects.create(product=product, name="Fabric", image_url="https://example.com/fabric.jpg")
        filter_type = FilterType.objects.create(name="Fabric Type", slug="fabric-type-feed-details")
        filter_option = FilterOption.objects.create(filter_type=filter_type, name="Fabric", slug="fabric-feed-details")
        ProductFilterValue.objects.create(product=product, filter_option=filter_option)

        xml = self._feed_xml()

        self.assertIn("<g:google_product_category>Furniture &gt; Sofas &amp; Couches</g:google_product_category>", xml)
        self.assertIn("<g:product_type>Sofas &gt; Reclining Sofas</g:product_type>", xml)
        self.assertIn("<g:description>The Turin Dark Grey Fabric recliner sofa.</g:description>", xml)
        self.assertIn("<g:attribute_name>Depth</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>92 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Seat Height</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>48 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Length</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>152 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Colour</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>Dark Grey</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Fabric Type</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>Fabric</g:attribute_value>", xml)

    def test_feed_uses_size_specific_dimension_details_for_variant_items(self):
        category = Category.objects.create(name="Chairs", slug="chairs-feed-details")
        product = Product.objects.create(
            name="Variant Dining Chair",
            slug="variant-dining-chair-feed-details",
            category=category,
            price="199.99",
            short_description="Dining chair",
            description="Dining chair description",
            dimensions=[
                {"measurement": "Seat Height", "values": {"Standard": "45 cm", "Tall": "50 cm"}},
            ],
        )
        ProductSize.objects.create(product=product, name="Standard", description="", price_delta="199.99")
        ProductSize.objects.create(product=product, name="Tall", description="", price_delta="249.99")

        xml = self._feed_xml()

        self.assertIn("<g:title>Variant Dining Chair - Standard</g:title>", xml)
        self.assertIn("<g:title>Variant Dining Chair - Tall</g:title>", xml)
        self.assertIn("<g:attribute_value>45 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_value>50 cm</g:attribute_value>", xml)

    def test_feed_extracts_safe_colour_and_fabric_without_guessing_materials(self):
        category = Category.objects.create(name="Sofas", slug="sofas-feed-title-extraction")
        subcategory = SubCategory.objects.create(
            name="Reclining Sofas",
            slug="reclining-sofas-feed-title-extraction",
            category=category,
        )
        Product.objects.create(
            name="Turin 2 Seater Dark Grey Fabric Recliner",
            slug="turin-2-seater-dark-grey-fabric-recliner-feed",
            category=category,
            subcategory=subcategory,
            price="499.00",
            short_description="Comfortable recliner sofa.",
            description="Comfortable recliner sofa description.",
        )

        xml = self._feed_xml()

        self.assertIn("<g:color>Dark Grey</g:color>", xml)
        self.assertIn("<g:attribute_name>Colour</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>Dark Grey</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Fabric Type</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>Fabric</g:attribute_value>", xml)
        self.assertNotIn("<g:material>Engineered Wood &amp; Fabric</g:material>", xml)
        self.assertNotIn("<g:frame_material>Engineered Wood</g:frame_material>", xml)

    def test_feed_extracts_safe_dimensions_from_dimension_paragraph(self):
        category = Category.objects.create(name="Sofas", slug="sofas-feed-dimension-paragraph")
        subcategory = SubCategory.objects.create(
            name="Reclining Sofas",
            slug="reclining-sofas-feed-dimension-paragraph",
            category=category,
        )
        Product.objects.create(
            name="Hannah Grey Fabric Corner Recliner Sofa",
            slug="hannah-grey-fabric-corner-recliner-sofa-feed",
            category=category,
            subcategory=subcategory,
            price="799.00",
            short_description="Grey fabric recliner sofa.",
            description="Grey fabric recliner sofa description.",
            dimension_paragraph="Length: 270 cm\n\nWidth: 235 cm\n\nDepth: 90 cm\n\nHeight: 90 cm\n\nSeat Height: 48 cm",
        )

        xml = self._feed_xml()

        self.assertIn("<g:attribute_name>Length</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>270 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Width</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>235 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Depth</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>90 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Seat Height</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>48 cm</g:attribute_value>", xml)

    def test_feed_uses_width_as_length_for_sofas_when_no_length_is_available(self):
        category = Category.objects.create(name="Sofas", slug="sofas-feed-width-length")
        subcategory = SubCategory.objects.create(
            name="Reclining Sofas",
            slug="reclining-sofas-feed-width-length",
            category=category,
        )
        Product.objects.create(
            name="Turin Grey Aire Leather Manual Recliner Armchair",
            slug="turin-grey-aire-leather-manual-recliner-armchair-feed",
            category=category,
            subcategory=subcategory,
            price="349.00",
            short_description="Grey aire leather recliner armchair.",
            description="Grey aire leather recliner armchair description.",
            dimension_paragraph="Width: 96 cm\n\nDepth: 97 cm\n\nHeight: 102 cm",
        )

        xml = self._feed_xml()

        self.assertIn("<g:attribute_name>Width</g:attribute_name>", xml)
        self.assertIn("<g:attribute_name>Length</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>96 cm</g:attribute_value>", xml)
        self.assertIn("<g:attribute_name>Depth</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>97 cm</g:attribute_value>", xml)

    def test_feed_prefers_specific_title_colour_over_broad_colour_data(self):
        category = Category.objects.create(name="Sofas", slug="sofas-feed-title-colour-priority")
        subcategory = SubCategory.objects.create(
            name="Reclining Sofas",
            slug="reclining-sofas-feed-title-colour-priority",
            category=category,
        )
        product = Product.objects.create(
            name="Turin Grey Aire Leather Manual Recliner Sofa",
            slug="turin-grey-aire-leather-manual-recliner-sofa-feed",
            category=category,
            subcategory=subcategory,
            price="499.00",
            short_description="Grey aire leather recliner sofa.",
            description="Grey aire leather recliner sofa description.",
        )
        ProductColor.objects.create(product=product, name="Black Family", hex_code="#000000")

        xml = self._feed_xml()

        self.assertIn("<g:color>Grey</g:color>", xml)
        self.assertIn("<g:attribute_value>Grey</g:attribute_value>", xml)
        self.assertNotIn("<g:color>Black Family</g:color>", xml)

    def test_feed_only_adds_frame_material_when_explicitly_stated(self):
        category = Category.objects.create(name="Sofas", slug="sofas-feed-frame-material")
        subcategory = SubCategory.objects.create(
            name="Reclining Sofas",
            slug="reclining-sofas-feed-frame-material",
            category=category,
        )
        Product.objects.create(
            name="Roma Recliner Sofa",
            slug="roma-recliner-sofa-feed-frame-material",
            category=category,
            subcategory=subcategory,
            price="699.00",
            short_description="Recliner sofa with a metal frame.",
            description="This recliner sofa has a metal frame and fabric upholstery.",
        )

        xml = self._feed_xml()

        self.assertIn("<g:frame_material>Metal</g:frame_material>", xml)
        self.assertIn("<g:attribute_name>Frame Material</g:attribute_name>", xml)
        self.assertIn("<g:attribute_value>Metal</g:attribute_value>", xml)


class CategoryFilterOptionOrderTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_category_filter_options_follow_category_specific_order(self):
        category = Category.objects.create(name="Dining", slug="dining-filter-option-order")
        filter_type = FilterType.objects.create(name="Seats", slug="seats-filter-option-order")
        two = FilterOption.objects.create(
            filter_type=filter_type,
            name="2 Seater",
            slug="two-seater-filter-option-order",
            display_order=1,
        )
        four = FilterOption.objects.create(
            filter_type=filter_type,
            name="4 Seater",
            slug="four-seater-filter-option-order",
            display_order=2,
        )
        six = FilterOption.objects.create(
            filter_type=filter_type,
            name="6 Seater",
            slug="six-seater-filter-option-order",
            display_order=3,
        )
        CategoryFilter.objects.create(
            category=category,
            filter_type=filter_type,
            option_order=[six.id, two.id, four.id],
        )

        response = APIClient().get(f"/api/categories/{category.slug}/filters/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [option["id"] for option in response.data["filters"][0]["options"]],
            [six.id, two.id, four.id],
        )

    def test_category_filter_rejects_options_from_another_filter_type(self):
        user = User.objects.create_user(username="filter-admin", password="password", is_staff=True)
        category = Category.objects.create(name="Beds", slug="beds-filter-option-validation")
        filter_type = FilterType.objects.create(name="Size", slug="size-filter-option-validation")
        other_type = FilterType.objects.create(name="Colour", slug="colour-filter-option-validation")
        foreign_option = FilterOption.objects.create(
            filter_type=other_type,
            name="Cream",
            slug="cream-filter-option-validation",
        )
        category_filter = CategoryFilter.objects.create(category=category, filter_type=filter_type)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.patch(
            f"/api/category-filters/{category_filter.id}/",
            {"option_order": [foreign_option.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        category_filter.refresh_from_db()
        self.assertEqual(category_filter.option_order, [])


class FilterOptionProductAssignmentTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="filter-option-admin", password="password", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_filter_option_products_endpoint_assigns_and_removes_products(self):
        category = Category.objects.create(name="Dining", slug="dining-filter-option-products")
        filter_type = FilterType.objects.create(name="Seats", slug="seats-filter-option-products")
        option = FilterOption.objects.create(filter_type=filter_type, name="4 Seater", slug="four-seater-products")
        first = Product.objects.create(
            name="Round Dining Table",
            slug="round-dining-table-filter-option",
            category=category,
            price="299.99",
            description="Dining table",
        )
        second = Product.objects.create(
            name="Oak Dining Table",
            slug="oak-dining-table-filter-option",
            category=category,
            price="399.99",
            description="Dining table",
        )

        response = self.client.patch(
            f"/api/filter-options/{option.id}/products/",
            {"product_ids": [first.id, second.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.data["assigned_product_ids"]), {first.id, second.id})
        self.assertTrue(ProductFilterValue.objects.filter(product=first, filter_option=option).exists())
        self.assertTrue(ProductFilterValue.objects.filter(product=second, filter_option=option).exists())

        response = self.client.patch(
            f"/api/filter-options/{option.id}/products/",
            {"product_ids": [second.id]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["assigned_product_ids"], [second.id])
        self.assertFalse(ProductFilterValue.objects.filter(product=first, filter_option=option).exists())
        self.assertTrue(ProductFilterValue.objects.filter(product=second, filter_option=option).exists())

    def test_filter_option_products_endpoint_returns_assigned_products(self):
        category = Category.objects.create(name="Beds", slug="beds-filter-option-products")
        filter_type = FilterType.objects.create(name="Size", slug="size-filter-option-products")
        option = FilterOption.objects.create(filter_type=filter_type, name="King", slug="king-products")
        product = Product.objects.create(
            name="King Bed",
            slug="king-bed-filter-option-products",
            category=category,
            price="499.99",
            description="Bed",
        )
        ProductFilterValue.objects.create(product=product, filter_option=option)

        response = self.client.get(f"/api/filter-options/{option.id}/products/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["assigned_product_ids"], [product.id])
        self.assertEqual(response.data["assigned_products"][0]["name"], "King Bed")
