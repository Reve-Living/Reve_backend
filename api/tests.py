from django.core import mail
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
)
class OrderEmailTests(TestCase):
    def test_cash_on_delivery_order_creation_sends_customer_and_admin_emails(self):
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
            "payment_method": "cod",
            "payment_id": "public-client-cannot-set-this",
            "status": "paid",
            "send_confirmation_email": False,
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
        self.assertEqual(len(customer_email.attachments), 1)
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

        response = APIClient().get(f"/api/products/?category={category.slug}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["rating"], 5.0)
        self.assertEqual(response.data[0]["review_count"], 1)


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
