import base64
import logging
import mimetypes
import re
from html import escape
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import Order

logger = logging.getLogger(__name__)

DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$")


def _format_money(value) -> str:
    try:
        return f"GBP {float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _humanize_key(key: str) -> str:
    return key.replace("_", " ").strip().title()


def _payment_label(method: str) -> str:
    return {
        "cod": "Cash on Delivery",
        "card": "Card",
        "paypal": "PayPal",
    }.get((method or "").lower(), method or "Not provided")


def _build_variant_lines(selected_variants: dict) -> list[str]:
    lines: list[str] = []
    for key, value in (selected_variants or {}).items():
        if value in (None, "", [], {}):
            continue
        if isinstance(value, list):
            rendered = ", ".join(str(v) for v in value)
        else:
            rendered = str(value)
        lines.append(f"{_humanize_key(str(key))}: {rendered}")
    return lines


def _dimension_detail_lines(raw_details: str) -> list[str]:
    return [part.strip() for part in (raw_details or "").split("|") if part.strip()]


def _recipient_intro(order: Order, recipient_label: str, is_admin: bool) -> tuple[str, str]:
    if is_admin:
        return (
            f"Hey {recipient_label}!",
            f"A new order has just been placed by {order.first_name} {order.last_name}.",
        )
    return (
        f"Hey {recipient_label}!",
        f"Your order is confirmed. We have received your order and our team will process it shortly.",
    )


def _order_items_text(order: Order) -> str:
    blocks: list[str] = []
    for idx, item in enumerate(order.items.select_related("product").all(), start=1):
        product_name = item.product.name if item.product else f"Product #{item.product_id or 'Unknown'}"
        lines = [
            f"{idx}. {product_name}",
            f"   Quantity: {item.quantity}",
            f"   Unit price: {_format_money(item.price)}",
        ]
        if item.size:
            lines.append(f"   Size: {item.size}")
        if item.color:
            lines.append(f"   Colour: {item.color}")
        if item.style:
            lines.append(f"   Style: {item.style}")
        if item.dimension:
            lines.append(f"   Dimension: {item.dimension}")
        for dimension_line in _dimension_detail_lines(item.dimension_details):
            lines.append(f"   {dimension_line}")
        if item.extras_total:
            lines.append(f"   Extras total: {_format_money(item.extras_total)}")
        lines.append(f"   Include dimension: {'Yes' if item.include_dimension else 'No'}")
        for variant_line in _build_variant_lines(item.selected_variants):
            lines.append(f"   {variant_line}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "No items found."


def _order_items_html(order: Order) -> str:
    cards: list[str] = []
    for idx, item in enumerate(order.items.select_related("product").all(), start=1):
        rows = [
            ("Quantity", item.quantity),
            ("Unit price", _format_money(item.price)),
        ]
        if item.size:
            rows.append(("Size", item.size))
        if item.color:
            rows.append(("Colour", item.color))
        if item.style:
            rows.append(("Style", item.style))
        if item.dimension:
            rows.append(("Dimension", item.dimension))
        if item.extras_total:
            rows.append(("Extras total", _format_money(item.extras_total)))
        rows.append(("Include dimension", "Yes" if item.include_dimension else "No"))
        for variant_line in _build_variant_lines(item.selected_variants):
            key, _, value = variant_line.partition(": ")
            rows.append((key, value))

        rendered_rows = "".join(
            f"<tr><td style='padding:6px 12px 6px 0; font-weight:600; vertical-align:top;'>{escape(str(label))}</td>"
            f"<td style='padding:6px 0; vertical-align:top;'>{escape(str(value))}</td></tr>"
            for label, value in rows
        )
        dimension_list = "".join(
            f"<li style='margin:0 0 6px;'>{escape(line)}</li>" for line in _dimension_detail_lines(item.dimension_details)
        )
        dimension_block = (
            "<div style='margin-top:12px;'>"
            "<p style='margin:0 0 8px; font-size:13px; font-weight:700;'>Dimensions</p>"
            f"<ul style='margin:0; padding-left:18px; font-size:14px;'>{dimension_list}</ul>"
            "</div>"
            if dimension_list
            else ""
        )
        product_name = item.product.name if item.product else f"Product #{item.product_id or 'Unknown'}"
        cards.append(
            "<div style='margin:0 0 18px; padding:16px; border:1px solid #e7e3dd; border-radius:12px;'>"
            f"<p style='margin:0 0 10px; font-size:16px; font-weight:700;'>{idx}. {escape(product_name)}</p>"
            f"<table style='width:100%; border-collapse:collapse; font-size:14px;'>{rendered_rows}</table>"
            f"{dimension_block}"
            "</div>"
        )
    return "".join(cards) or "<p>No items found.</p>"


def _message_subject(order: Order) -> str:
    return f"Order ORD-{order.id} confirmation"


def _message_text(order: Order, recipient_label: str, is_admin: bool) -> str:
    greeting, intro = _recipient_intro(order, recipient_label, is_admin)
    payment_id = order.payment_id or "Not available yet"
    reference_images_count = len(order.reference_images or [])
    return (
        f"{greeting}\n\n"
        f"{intro}\n\n"
        f"Order number: ORD-{order.id}\n"
        f"Created at: {order.created_at:%Y-%m-%d %H:%M:%S} UTC\n"
        f"Status: {order.status}\n"
        f"Payment method: {_payment_label(order.payment_method)}\n"
        f"Payment ID: {payment_id}\n"
        f"Order total: {_format_money(order.total_amount)}\n"
        f"Delivery charges: {_format_money(order.delivery_charges)}\n\n"
        f"Customer details\n"
        f"Name: {order.first_name} {order.last_name}\n"
        f"Email: {order.email}\n"
        f"Phone: {order.phone}\n"
        f"Address: {order.address}\n"
        f"City: {order.city}\n"
        f"Postal code: {order.postal_code}\n\n"
        f"Order notes\n"
        f"Special notes: {order.special_notes or 'None'}\n"
        f"Reference images attached: {reference_images_count}\n\n"
        f"Items ordered\n"
        f"{_order_items_text(order)}\n\n"
        "Thank you,\nREVE Living"
    )


def _message_html(order: Order, recipient_label: str, is_admin: bool) -> str:
    greeting, intro = _recipient_intro(order, recipient_label, is_admin)
    payment_id = order.payment_id or "Not available yet"
    reference_images_count = len(order.reference_images or [])
    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif; color:#2f241c; line-height:1.5;">
      <h2 style="margin:0 0 16px;">Order ORD-{order.id} confirmation</h2>
      <p style="margin:0 0 8px; font-size:18px; font-weight:700;">{escape(greeting)}</p>
      <p style="margin:0 0 20px;">{escape(intro)}</p>

      <div style="margin:0 0 20px; padding:16px; border:1px solid #e7e3dd; border-radius:12px; background:#fbf9f6;">
        <p style="margin:0 0 8px;"><strong>Created at:</strong> {order.created_at:%Y-%m-%d %H:%M:%S} UTC</p>
        <p style="margin:0 0 8px;"><strong>Status:</strong> {escape(order.status)}</p>
        <p style="margin:0 0 8px;"><strong>Payment method:</strong> {escape(_payment_label(order.payment_method))}</p>
        <p style="margin:0 0 8px;"><strong>Payment ID:</strong> {escape(payment_id)}</p>
        <p style="margin:0 0 8px;"><strong>Order total:</strong> {escape(_format_money(order.total_amount))}</p>
        <p style="margin:0;"><strong>Delivery charges:</strong> {escape(_format_money(order.delivery_charges))}</p>
      </div>

      <h3 style="margin:0 0 12px;">Customer details</h3>
      <div style="margin:0 0 20px; padding:16px; border:1px solid #e7e3dd; border-radius:12px;">
        <p style="margin:0 0 8px;"><strong>Name:</strong> {escape(order.first_name)} {escape(order.last_name)}</p>
        <p style="margin:0 0 8px;"><strong>Email:</strong> {escape(order.email)}</p>
        <p style="margin:0 0 8px;"><strong>Phone:</strong> {escape(order.phone)}</p>
        <p style="margin:0 0 8px;"><strong>Address:</strong> {escape(order.address)}</p>
        <p style="margin:0 0 8px;"><strong>City:</strong> {escape(order.city)}</p>
        <p style="margin:0;"><strong>Postal code:</strong> {escape(order.postal_code)}</p>
      </div>

      <h3 style="margin:0 0 12px;">Order notes</h3>
      <div style="margin:0 0 20px; padding:16px; border:1px solid #e7e3dd; border-radius:12px;">
        <p style="margin:0 0 10px;"><strong>Special notes:</strong> {escape(order.special_notes or "None")}</p>
        <p style="margin:0;"><strong>Reference images attached:</strong> {reference_images_count}</p>
      </div>

      <h3 style="margin:0 0 12px;">Items ordered</h3>
      {_order_items_html(order)}

      <p style="margin:24px 0 0;">Thank you,<br />REVE Living</p>
    </div>
    """


def _reference_image_attachments(reference_images: Iterable[str]) -> list[tuple[str, bytes, str]]:
    attachments: list[tuple[str, bytes, str]] = []
    for idx, raw in enumerate(reference_images or [], start=1):
        if not raw:
            continue
        match = DATA_URL_RE.match(raw)
        if not match:
            continue
        mime = match.group("mime")
        try:
            decoded = base64.b64decode(match.group("data"))
        except (ValueError, base64.binascii.Error):
            logger.warning("Skipping invalid reference image payload at index %s", idx)
            continue
        ext = mimetypes.guess_extension(mime) or ".bin"
        attachments.append((f"reference-image-{idx}{ext}", decoded, mime))
    return attachments


def _send_email(to_email: str, order: Order, recipient_label: str, is_admin: bool) -> None:
    subject = _message_subject(order)
    text_body = _message_text(order, recipient_label, is_admin)
    html_body = _message_html(order, recipient_label, is_admin)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    email.attach_alternative(html_body, "text/html")
    for filename, content, mime in _reference_image_attachments(order.reference_images):
        email.attach(filename, content, mime)
    email.send(fail_silently=False)


def send_order_confirmation_emails(order_id: int) -> None:
    if not getattr(settings, "EMAIL_HOST", "") or not getattr(settings, "DEFAULT_FROM_EMAIL", ""):
        logger.warning("Email settings are incomplete; skipping order confirmation for order %s", order_id)
        return

    order = Order.objects.prefetch_related("items__product").get(pk=order_id)
    recipients = [(order.email, order.first_name or "Customer", False)]
    admin_email = getattr(settings, "ORDER_NOTIFICATION_EMAIL", "") or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    if admin_email and admin_email.lower() != (order.email or "").lower():
        recipients.append((admin_email, "Team", True))

    for to_email, label, is_admin in recipients:
        try:
            _send_email(to_email, order, label, is_admin)
        except Exception:
            logger.exception("Failed to send order confirmation email for order %s to %s", order_id, to_email)
