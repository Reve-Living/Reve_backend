from typing import Any

import requests
import stripe
from django.conf import settings
from requests.adapters import HTTPAdapter, Retry

class PaymentProviderError(Exception):
    pass


_PAYPAL_SESSION: requests.Session | None = None


def _object_get(value: Any, key: str, default=None):
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _paypal_session() -> requests.Session:
    global _PAYPAL_SESSION

    if _PAYPAL_SESSION is not None:
        return _PAYPAL_SESSION

    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retries)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    _PAYPAL_SESSION = session
    return session


def paypal_request(method: str, path: str, **kwargs):
    timeout = kwargs.pop(
        "timeout",
        (
            getattr(settings, "PAYPAL_CONNECT_TIMEOUT", 5),
            getattr(settings, "PAYPAL_TIMEOUT", 15),
        ),
    )
    url = f"{settings.PAYPAL_BASE_URL}{path}"
    try:
        response = _paypal_session().request(method, url, timeout=timeout, **kwargs)
    except requests.Timeout:
        return None, {"error": "PayPal timed out. Please try again in a moment."}
    except requests.RequestException as exc:
        return None, {"error": f"PayPal request failed: {exc}"}

    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return None, {
            "error": "PayPal returned an error",
            "status": response.status_code,
            "body": body,
        }
    return response, None


def paypal_access_token():
    if not settings.PAYPAL_CLIENT_ID or not settings.PAYPAL_CLIENT_SECRET:
        return None, {"error": "Missing PayPal credentials", "hint": "Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET"}

    response, error = paypal_request(
        "POST",
        "/v1/oauth2/token",
        auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
    )
    if error:
        return None, error
    return response.json().get("access_token"), None


def _require_paypal_access_token() -> str:
    token, error = paypal_access_token()
    if token:
        return token
    message = (error or {}).get("error") or "PayPal auth failed"
    raise PaymentProviderError(message)


def extract_paypal_capture(payload: dict | None) -> dict:
    for purchase_unit in (payload or {}).get("purchase_units") or []:
        payments = purchase_unit.get("payments") or {}
        captures = payments.get("captures") or []
        if captures:
            capture = captures[0] or {}
            if capture.get("id"):
                return capture
    return {}


def extract_paypal_capture_id(payload: dict | None) -> str:
    return str(extract_paypal_capture(payload).get("id") or "").strip()


def extract_local_order_id_from_paypal(payload: dict | None):
    for purchase_unit in (payload or {}).get("purchase_units") or []:
        custom_id = str(purchase_unit.get("custom_id") or "").strip()
        if custom_id.isdigit():
            return int(custom_id)
    return None


def get_paypal_order_details(paypal_order_id: str) -> dict:
    access_token = _require_paypal_access_token()
    response, error = paypal_request(
        "GET",
        f"/v2/checkout/orders/{paypal_order_id}",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    if error:
        message = error.get("error") or "Failed to fetch PayPal order details"
        raise PaymentProviderError(message)
    return response.json()


def get_stripe_payment_details(payment_id: str = "", payment_metadata: dict | None = None) -> dict:
    stripe.api_key = settings.STRIPE_SECRET_KEY
    if not stripe.api_key:
        raise PaymentProviderError("Stripe is not configured")

    metadata = dict(payment_metadata or {})
    candidate = str(payment_id or "").strip()
    session_id = str(metadata.get("stripe_checkout_session_id") or "").strip()
    payment_intent_id = str(metadata.get("stripe_payment_intent_id") or "").strip()

    if candidate.startswith("cs_"):
        session_id = candidate
    elif candidate.startswith("pi_"):
        payment_intent_id = candidate

    if session_id:
        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except Exception as exc:
            raise PaymentProviderError(f"Stripe session lookup failed: {exc}") from exc

        metadata["stripe_checkout_session_id"] = str(_object_get(session, "id") or session_id)
        payment_status = str(_object_get(session, "payment_status") or "").strip()
        if payment_status:
            metadata["stripe_payment_status"] = payment_status
        payment_intent = _object_get(session, "payment_intent")
        resolved_payment_intent_id = str(_object_get(payment_intent, "id", payment_intent) or "").strip()
        if resolved_payment_intent_id:
            metadata["stripe_payment_intent_id"] = resolved_payment_intent_id
        return metadata

    if payment_intent_id:
        metadata["stripe_payment_intent_id"] = payment_intent_id
        return metadata

    raise PaymentProviderError("Missing Stripe payment reference")


def resolve_paypal_payment_details(payment_id: str = "", payment_metadata: dict | None = None) -> dict:
    metadata = dict(payment_metadata or {})
    candidate = str(payment_id or "").strip()
    capture_id = str(metadata.get("paypal_capture_id") or "").strip()
    paypal_order_id = str(metadata.get("paypal_order_id") or "").strip()

    if capture_id and not paypal_order_id:
        metadata["paypal_capture_id"] = capture_id
        return metadata

    order_id_candidates = [paypal_order_id]
    if candidate:
        order_id_candidates.append(candidate)

    for order_id in [value for value in order_id_candidates if value]:
        try:
            order_payload = get_paypal_order_details(order_id)
        except PaymentProviderError:
            continue

        capture = extract_paypal_capture(order_payload)
        resolved_capture_id = str(capture.get("id") or "").strip()
        if resolved_capture_id:
            metadata["paypal_order_id"] = order_id
            metadata["paypal_capture_id"] = resolved_capture_id
            capture_status = str(capture.get("status") or "").strip()
            if capture_status:
                metadata["paypal_capture_status"] = capture_status
            return metadata

    if candidate:
        metadata["paypal_capture_id"] = candidate
        return metadata

    raise PaymentProviderError("Missing PayPal payment reference")


def refund_stripe_payment(*, order_id: int, payment_id: str = "", payment_metadata: dict | None = None) -> dict:
    metadata = get_stripe_payment_details(payment_id=payment_id, payment_metadata=payment_metadata)
    payment_status = str(metadata.get("stripe_payment_status") or "").strip().lower()
    if payment_status and payment_status != "paid":
        return {
            "status": "not_required",
            "provider": "stripe",
            "payment_metadata": metadata,
            "error": "",
            "refund_id": "",
        }

    payment_intent_id = str(metadata.get("stripe_payment_intent_id") or "").strip()
    if not payment_intent_id:
        raise PaymentProviderError("Missing Stripe PaymentIntent required for refund")

    try:
        refund = stripe.Refund.create(
            payment_intent=payment_intent_id,
            reason="requested_by_customer",
            metadata={"order_id": str(order_id)},
        )
    except Exception as exc:
        raise PaymentProviderError(f"Stripe refund failed: {exc}") from exc

    return {
        "status": "succeeded",
        "provider": "stripe",
        "payment_metadata": metadata,
        "refund_id": str(_object_get(refund, "id") or "").strip(),
        "error": "",
    }


def refund_paypal_payment(*, order_id: int, payment_id: str = "", payment_metadata: dict | None = None) -> dict:
    del order_id

    metadata = resolve_paypal_payment_details(payment_id=payment_id, payment_metadata=payment_metadata)
    capture_id = str(metadata.get("paypal_capture_id") or "").strip()
    if not capture_id:
        raise PaymentProviderError("Missing PayPal capture ID required for refund")

    access_token = _require_paypal_access_token()
    response, error = paypal_request(
        "POST",
        f"/v2/payments/captures/{capture_id}/refund",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={},
    )
    if error:
        body = error.get("body")
        if isinstance(body, dict):
            message = body.get("message") or error.get("error") or "PayPal refund failed"
        else:
            message = error.get("error") or "PayPal refund failed"
        raise PaymentProviderError(message)

    payload = response.json()
    return {
        "status": "succeeded",
        "provider": "paypal",
        "payment_metadata": metadata,
        "refund_id": str(payload.get("id") or "").strip(),
        "error": "",
    }
