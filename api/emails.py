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
COMPANY_PHONE = "+44 7386 340475"
COMPANY_SUPPORT_EMAIL = "support@reveliving.co.uk"


def _payment_label(method: str) -> str:
    return {
        "cod": "Cash on Delivery",
        "card": "Card",
        "paypal": "PayPal",
    }.get((method or "").lower(), method or "Not provided")


def _message_subject(order: Order) -> str:
    return f"Order Confirmation - Reve Living (Order #{order.id})"


def _format_pounds(value) -> str:
    try:
        return f"\u00a3{float(value):.2f}"
    except (TypeError, ValueError):
        return f"\u00a3{value}"


def _customer_name(order: Order) -> str:
    return f"{order.first_name} {order.last_name}".strip()


def _order_items_rows_text(order: Order) -> str:
    rows: list[str] = []
    for item in order.items.select_related("product").all():
        product_name = item.product.name if item.product else f"Product #{item.product_id or 'Unknown'}"
        rows.append(f"{product_name} | {item.quantity} | {_format_pounds(item.price)}")
    return "\n".join(rows) if rows else "No products found."


def _order_items_rows_html(order: Order) -> str:
    rows: list[str] = []
    for item in order.items.select_related("product").all():
        product_name = item.product.name if item.product else f"Product #{item.product_id or 'Unknown'}"
        rows.append(
            "<tr>"
            f"<td style='border:1px solid #808080; padding:6px 8px;'>{escape(product_name)}</td>"
            f"<td style='border:1px solid #808080; padding:6px 8px;'>{item.quantity}</td>"
            f"<td style='border:1px solid #808080; padding:6px 8px;'>{escape(_format_pounds(item.price))}</td>"
            "</tr>"
        )
    if not rows:
        rows.append(
            "<tr>"
            "<td style='border:1px solid #808080; padding:6px 8px;'>No products found.</td>"
            "<td style='border:1px solid #808080; padding:6px 8px;'>-</td>"
            "<td style='border:1px solid #808080; padding:6px 8px;'>-</td>"
            "</tr>"
        )
    return "".join(rows)


def _message_text(order: Order, recipient_label: str, is_admin: bool) -> str:
    del recipient_label

    intro_lines = (
        [
            "REVE LIVING",
            "A new order has been received and is being processed.",
            "",
        ]
        if is_admin
        else [
            "REVE LIVING",
            "Thank you for your order.",
            "Your order has been received and is being processed.",
            "",
        ]
    )

    return (
        "2. EMAIL AUTOMATION - CUSTOMER ORDER CONFIRMATION\n\n"
        f"Subject: {_message_subject(order)}\n\n"
        + "\n".join(intro_lines)
        + "\n"
        + "Order Details\n\n"
        + f"Order Number: {order.id}\n"
        + f"Order Date: {order.created_at:%d %B %Y}\n\n"
        + "Delivery Address\n\n"
        + f"{_customer_name(order)}\n"
        + f"{order.address}\n"
        + f"{order.city}\n"
        + f"{order.postal_code}\n\n"
        + "Products Ordered\n\n"
        + "Product Name | Quantity | Price\n"
        + f"{_order_items_rows_text(order)}\n\n"
        + "Payment Information\n\n"
        + f"Payment Method: {_payment_label(order.payment_method)}\n"
        + f"Total Amount: {_format_pounds(order.total_amount)}\n\n"
        + "Delivery will be made within the estimated delivery timeframe for your order.\n"
        + "If anything changes, you will be notified.\n\n"
        + "Important\n\n"
        + "If any of the above information is incorrect, please contact Reve Living as soon as possible.\n"
        + f"Email: {COMPANY_SUPPORT_EMAIL}\n"
        + f"Phone: {COMPANY_PHONE}"
    )


def _message_html(order: Order, recipient_label: str, is_admin: bool) -> str:
    del recipient_label

    intro_block = (
        "<p style='margin:0 0 4px; font-weight:700;'>REVE LIVING</p>"
        "<p style='margin:0;'>A new order has been received and is being processed.</p>"
        if is_admin
        else "<p style='margin:0 0 4px; font-weight:700;'>REVE LIVING</p>"
        "<p style='margin:0;'>Thank you for your order.</p>"
        "<p style='margin:0;'>Your order has been received and is being processed.</p>"
    )

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif; color:#000000; line-height:1.35; font-size:14px; max-width:760px;">
      <h2 style="margin:0 0 18px; font-size:28px; font-weight:800; text-transform:uppercase;">2. EMAIL AUTOMATION - CUSTOMER ORDER CONFIRMATION</h2>
      <p style="margin:0 0 24px;"><strong>Subject:</strong> {escape(_message_subject(order))}</p>

      <div style="margin:0 0 26px;">
        {intro_block}
      </div>

      <h3 style="margin:0 0 140px; font-size:32px; font-style:italic; font-weight:500;">Order Details</h3>

      <div style="border-top:8px solid #222222; margin:0 0 40px;"></div>

      <div style="margin:0 0 28px;">
        <p style="margin:0 0 4px;">Order Number: {order.id}</p>
        <p style="margin:0;">Order Date: {order.created_at:%d %B %Y}</p>
      </div>

      <h3 style="margin:0 0 18px; font-size:32px; font-style:italic; font-weight:500;">Delivery Address</h3>
      <div style="margin:0 0 28px;">
        <p style="margin:0;">{escape(_customer_name(order))}</p>
        <p style="margin:0;">{escape(order.address)}</p>
        <p style="margin:0;">{escape(order.city)}</p>
        <p style="margin:0;">{escape(order.postal_code)}</p>
      </div>

      <h3 style="margin:0 0 18px; font-size:32px; font-style:italic; font-weight:500;">Products Ordered</h3>
      <table style="border-collapse:collapse; width:100%; max-width:560px; margin:0 0 34px;">
        <thead>
          <tr>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:6px 8px; text-align:left; font-weight:400;">Product Name</th>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:6px 8px; text-align:left; font-weight:400;">Quantity</th>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:6px 8px; text-align:left; font-weight:400;">Price</th>
          </tr>
        </thead>
        <tbody>
          {_order_items_rows_html(order)}
        </tbody>
      </table>

      <h3 style="margin:0 0 18px; font-size:32px; font-style:italic; font-weight:500;">Payment Information</h3>
      <div style="margin:0 0 28px;">
        <p style="margin:0;">Payment Method: {escape(_payment_label(order.payment_method))}</p>
        <p style="margin:0;">Total Amount: {escape(_format_pounds(order.total_amount))}</p>
      </div>

      <p style="margin:0 0 28px;">Delivery will be made within the estimated delivery timeframe for your order.<br />If anything changes, you will be notified.</p>

      <h3 style="margin:0 0 18px; font-size:32px; font-style:italic; font-weight:500;">Important</h3>
      <p style="margin:0;">If any of the above information is incorrect, please contact Reve Living as soon as possible.</p>
      <p style="margin:0;">Email: {escape(COMPANY_SUPPORT_EMAIL)}</p>
      <p style="margin:0;">Phone: {escape(COMPANY_PHONE)}</p>
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
