import base64
import logging
import mimetypes
import re
from html import escape
from typing import Iterable

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils import timezone

from .models import Order

logger = logging.getLogger(__name__)

DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$")
COMPANY_PHONE = "+44 7386 340475"
COMPANY_SUPPORT_EMAIL = "support@reveliving.co.uk"


def _payment_label(method: str) -> str:
    return {
        "paid": "Paid",
        "bank_transfer": "Bank Transfer",
        "cash": "Cash",
        "cash_on_delivery": "Cash on Delivery",
        "cod": "Cash on Delivery",
        "card": "Card",
        "manual": "Manual",
        "paypal": "PayPal",
        "google_pay": "Google Pay",
        "klarna": "Klarna",
    }.get((method or "").lower(), method or "Not provided")


def _message_subject(order: Order) -> str:
    return f"Order Confirmation - Reve Living (Order #{order.id})"


def _cancellation_subject(order: Order) -> str:
    return f"Order Cancelled - Reve Living (Order #{order.id})"


def _format_pounds(value) -> str:
    try:
        return f"\u00a3{float(value):.2f}"
    except (TypeError, ValueError):
        return f"\u00a3{value}"


def _customer_name(order: Order) -> str:
    return f"{order.first_name} {order.last_name}".strip()


def _format_order_datetime(value) -> str:
    if not value:
        return "-"
    current = timezone.localtime(value) if timezone.is_aware(value) else value
    return current.strftime("%d %B %Y at %I:%M %p")


def _refund_status_label(order: Order) -> str:
    status = str(order.refund_status or "").strip().lower()
    if status == "succeeded":
        return "Refund initiated to the original payment method"
    if status == "failed":
        return "Refund needs manual follow-up"
    if status == "not_required":
        return "No automatic refund was needed"
    return "Not available"


def _order_items_rows_text(order: Order) -> str:
    rows: list[str] = []
    for item in order.items.select_related("product").all():
        product_name = item.product.name if item.product else f"Product #{item.product_id or 'Unknown'}"
        assembly_suffix = ""
        if item.assembly_service_selected:
            assembly_suffix = f" | Assembly Service: {_format_pounds(item.assembly_service_price)}"
        rows.append(f"{product_name} | {item.quantity} | {_format_pounds(item.price)}{assembly_suffix}")
    return "\n".join(rows) if rows else "No products found."


def _order_items_rows_html(order: Order) -> str:
    rows: list[str] = []
    for item in order.items.select_related("product").all():
        product_name = item.product.name if item.product else f"Product #{item.product_id or 'Unknown'}"
        price_html = escape(_format_pounds(item.price))
        if item.assembly_service_selected:
            price_html = f"{price_html}<br /><span style='font-size:12px; color:#555;'>Assembly: {escape(_format_pounds(item.assembly_service_price))}</span>"
        rows.append(
            "<tr>"
            f"<td style='border:1px solid #808080; padding:6px 8px;'>{escape(product_name)}</td>"
            f"<td style='border:1px solid #808080; padding:6px 8px;'>{item.quantity}</td>"
            f"<td style='border:1px solid #808080; padding:6px 8px;'>{price_html}</td>"
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


def _line_break_html(value: str) -> str:
    lines = [escape(line.strip()) for line in str(value or "").splitlines() if line.strip()]
    return "<br />".join(lines) if lines else "-"


def _append_unique(parts: list[str], seen: set[str], value: str) -> None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return
    key = cleaned.lower()
    if key in seen:
        return
    seen.add(key)
    parts.append(cleaned)


def _is_displayable_order_part(value: str) -> bool:
    cleaned = str(value or "").strip()
    if not cleaned:
        return False
    lower = cleaned.lower()
    if "dimension" in lower:
        return False
    if re.search(r"(^|\b)(length|width|height|headboard height|bed height)\s*:", lower):
        return False
    if re.search(r"(cm|inch|inches|\")", lower) and re.search(r"(length|width|height)", lower):
        return False
    return True


def _order_part_rank(value: str) -> int:
    lower = str(value or "").strip().lower()
    if lower.startswith("size:"):
        return 1
    if lower.startswith("colour:") or lower.startswith("color:"):
        return 2
    if lower.startswith("fabric:"):
        return 3
    if "storage" in lower:
        return 4
    if "headboard" in lower:
        return 5
    if lower.startswith("mattress"):
        return 6
    if lower.startswith("assembly service"):
        return 7
    return 99


def _sort_order_parts(parts: list[str]) -> list[str]:
    return sorted(parts, key=lambda part: (_order_part_rank(part), part.lower()))


def _normalized_style_parts(style_summary: str) -> list[str]:
    parts: list[str] = []
    seen: set[str] = set()
    for raw_part in str(style_summary or "").split("|"):
        cleaned = raw_part.strip()
        if not cleaned:
            continue
        if not _is_displayable_order_part(cleaned):
            continue
        _append_unique(parts, seen, cleaned)
    return _sort_order_parts(parts)


def _item_description_html(order: Order) -> str:
    lines: list[str] = []
    for item in order.items.select_related("product").all():
        product_name = item.product.name if item.product else f"Product #{item.product_id or 'Unknown'}"
        parts = [f"{item.quantity}x {product_name}"]
        seen = {parts[0].lower()}
        if item.size:
            _append_unique(parts, seen, f"Size: {item.size}")
        if item.color:
            _append_unique(parts, seen, f"Colour: {item.color}")
        for style_part in _normalized_style_parts(item.style):
            _append_unique(parts, seen, style_part)
        if item.extras_total:
            try:
                if float(item.extras_total) > 0:
                    _append_unique(parts, seen, f"Extras: {_format_pounds(item.extras_total)}")
            except (TypeError, ValueError):
                pass
        if item.assembly_service_selected:
            _append_unique(parts, seen, f"Assembly Service: {_format_pounds(item.assembly_service_price)}")
        lines.append(" | ".join(_sort_order_parts(parts)))
    return "<br /><br />".join(escape(line) for line in lines) if lines else "No products found."


def _label_value_row_html(label: str, value: str, tall: bool = False) -> str:
    padding = "12px 14px" if tall else "10px 14px"
    return (
        "<tr>"
        f"<td style='width:38%; border:1px solid #d6d6d6; padding:{padding}; vertical-align:top; "
        "font-weight:600; background:#fafafa;'>"
        f"{escape(label)}"
        "</td>"
        f"<td style='width:62%; border:1px solid #d6d6d6; padding:{padding}; vertical-align:top;'>"
        f"{value or '-'}"
        "</td>"
        "</tr>"
    )


def _section_heading_row_html(title: str) -> str:
    return (
        "<tr>"
        f"<td colspan='2' style='border:1px solid #d6d6d6; padding:12px 14px; background:#f1f1f1; "
        "font-size:15px; font-weight:700;'>"
        f"{escape(title)}"
        "</td>"
        "</tr>"
    )


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
        "\n".join(intro_lines)
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

    delivery_address = "<br />".join(
        [
            escape(part)
            for part in [
                _customer_name(order),
                order.address,
                order.city,
                order.postal_code,
            ]
            if str(part or "").strip()
        ]
    )
    order_rows = [
        _section_heading_row_html("Order Details"),
        _label_value_row_html("Order Number", escape(str(order.id))),
        _label_value_row_html("Order Date", escape(f"{order.created_at:%d %B %Y}")),
        _label_value_row_html("Customer Name", escape(_customer_name(order))),
        _label_value_row_html("Phone Number", escape(order.phone or "-")),
        _label_value_row_html("Alternative Phone Number", escape(order.alternative_phone or "-")),
        _label_value_row_html("Email Address", escape(order.email or "-")),
        _label_value_row_html("Street Address", delivery_address or "-"),
        _label_value_row_html("Customer Floor Number", escape(order.floor_number or "-")),
        _label_value_row_html("Special Notes", _line_break_html(order.special_notes), tall=True),
        _label_value_row_html("Product Description", _item_description_html(order), tall=True),
        _label_value_row_html("Delivery Charges", escape(_format_pounds(order.delivery_charges))),
        _label_value_row_html("Payment Method", escape(_payment_label(order.payment_method))),
        _label_value_row_html("Total Amount", escape(_format_pounds(order.total_amount))),
    ]

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif; color:#000000; line-height:1.35; font-size:14px; max-width:760px;">
      <div style="margin:0 0 26px;">
        {intro_block}
      </div>

      <table style="border-collapse:collapse; width:100%; max-width:760px; margin:0 0 34px;">
        <tbody>
          {''.join(order_rows)}
        </tbody>
      </table>

      <h3 style="margin:0 0 18px; font-size:24px; font-style:italic; font-weight:500;">Products Ordered</h3>
      <table style="border-collapse:collapse; width:100%; max-width:760px; margin:0 0 34px;">
        <thead>
          <tr>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:8px 10px; text-align:left; font-weight:600;">Product Name</th>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:8px 10px; text-align:left; font-weight:600;">Quantity</th>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:8px 10px; text-align:left; font-weight:600;">Price</th>
          </tr>
        </thead>
        <tbody>
          {_order_items_rows_html(order)}
        </tbody>
      </table>

      <p style="margin:0 0 28px;">Delivery will be made within the estimated delivery timeframe for your order.<br />If anything changes, you will be notified.</p>

      <h3 style="margin:0 0 18px; font-size:24px; font-style:italic; font-weight:500;">Important</h3>
      <p style="margin:0;">If any of the above information is incorrect, please contact Reve Living as soon as possible.</p>
      <p style="margin:0;">Email: {escape(COMPANY_SUPPORT_EMAIL)}</p>
      <p style="margin:0;">Phone: {escape(COMPANY_PHONE)}</p>
    </div>
    """


def _cancellation_message_text(order: Order, recipient_label: str, is_admin: bool) -> str:
    del recipient_label

    intro_lines = (
        [
            "REVE LIVING",
            "An order has been cancelled.",
            "",
        ]
        if is_admin
        else [
            "REVE LIVING",
            "Your order has been cancelled.",
            "",
        ]
    )

    cancellation_date = _format_order_datetime(order.cancelled_at)

    return (
        "\n".join(intro_lines)
        + "\n"
        + "Cancellation Details\n\n"
        + f"Order Number: {order.id}\n"
        + f"Order Date: {order.created_at:%d %B %Y}\n"
        + f"Cancellation Date: {cancellation_date}\n"
        + f"Order Status: Cancelled\n\n"
        + "Refund Information\n\n"
        + f"Refund Status: {_refund_status_label(order)}\n"
        + (f"Refund Date: {_format_order_datetime(order.refunded_at)}\n" if order.refunded_at else "")
        + "\n"
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
        + "If you need any help with this cancellation, please contact Reve Living.\n"
        + f"Email: {COMPANY_SUPPORT_EMAIL}\n"
        + f"Phone: {COMPANY_PHONE}"
    )


def _cancellation_message_html(order: Order, recipient_label: str, is_admin: bool) -> str:
    del recipient_label

    intro_block = (
        "<p style='margin:0 0 4px; font-weight:700;'>REVE LIVING</p>"
        "<p style='margin:0;'>An order has been cancelled.</p>"
        if is_admin
        else "<p style='margin:0 0 4px; font-weight:700;'>REVE LIVING</p>"
        "<p style='margin:0;'>Your order has been cancelled.</p>"
    )

    delivery_address = "<br />".join(
        [
            escape(part)
            for part in [
                _customer_name(order),
                order.address,
                order.city,
                order.postal_code,
            ]
            if str(part or "").strip()
        ]
    )
    cancellation_rows = [
        _section_heading_row_html("Cancellation Details"),
        _label_value_row_html("Order Number", escape(str(order.id))),
        _label_value_row_html("Order Date", escape(f"{order.created_at:%d %B %Y}")),
        _label_value_row_html("Cancellation Date", escape(_format_order_datetime(order.cancelled_at))),
        _label_value_row_html("Order Status", "Cancelled"),
        _label_value_row_html("Refund Status", escape(_refund_status_label(order))),
        _label_value_row_html("Refund Date", escape(_format_order_datetime(order.refunded_at))),
        _label_value_row_html("Customer Name", escape(_customer_name(order))),
        _label_value_row_html("Phone Number", escape(order.phone or "-")),
        _label_value_row_html("Alternative Phone Number", escape(order.alternative_phone or "-")),
        _label_value_row_html("Email Address", escape(order.email or "-")),
        _label_value_row_html("Street Address", delivery_address or "-"),
        _label_value_row_html("Customer Floor Number", escape(order.floor_number or "-")),
        _label_value_row_html("Special Notes", _line_break_html(order.special_notes), tall=True),
        _label_value_row_html("Product Description", _item_description_html(order), tall=True),
        _label_value_row_html("Payment Method", escape(_payment_label(order.payment_method))),
        _label_value_row_html("Total Amount", escape(_format_pounds(order.total_amount))),
    ]

    return f"""
    <div style="font-family:Arial,Helvetica,sans-serif; color:#000000; line-height:1.35; font-size:14px; max-width:760px;">
      <div style="margin:0 0 26px;">
        {intro_block}
      </div>

      <table style="border-collapse:collapse; width:100%; max-width:760px; margin:0 0 34px;">
        <tbody>
          {''.join(cancellation_rows)}
        </tbody>
      </table>

      <h3 style="margin:0 0 18px; font-size:24px; font-style:italic; font-weight:500;">Products Ordered</h3>
      <table style="border-collapse:collapse; width:100%; max-width:760px; margin:0 0 34px;">
        <thead>
          <tr>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:8px 10px; text-align:left; font-weight:600;">Product Name</th>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:8px 10px; text-align:left; font-weight:600;">Quantity</th>
            <th style="border:1px solid #808080; background:#e9e9e9; padding:8px 10px; text-align:left; font-weight:600;">Price</th>
          </tr>
        </thead>
        <tbody>
          {_order_items_rows_html(order)}
        </tbody>
      </table>

      <p style="margin:0 0 28px;">If you need any help with this cancellation, please contact Reve Living.</p>
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


def _send_cancellation_email(to_email: str, order: Order, recipient_label: str, is_admin: bool) -> None:
    subject = _cancellation_subject(order)
    text_body = _cancellation_message_text(order, recipient_label, is_admin)
    html_body = _cancellation_message_html(order, recipient_label, is_admin)

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to_email],
    )
    email.attach_alternative(html_body, "text/html")
    email.send(fail_silently=False)


def _build_recipients(order: Order) -> list[tuple[str, str, bool]]:
    recipients = []
    customer_email = (order.email or "").strip()
    if customer_email:
        recipients.append((customer_email, order.first_name or "Customer", False))
    admin_email = getattr(settings, "ORDER_NOTIFICATION_EMAIL", "") or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    if admin_email and admin_email.lower() != customer_email.lower():
        recipients.append((admin_email, "Team", True))
    return recipients


def send_order_confirmation_emails(order_id: int) -> None:
    if not getattr(settings, "EMAIL_HOST", "") or not getattr(settings, "DEFAULT_FROM_EMAIL", ""):
        logger.warning("Email settings are incomplete; skipping order confirmation for order %s", order_id)
        return

    order = Order.objects.prefetch_related("items__product").get(pk=order_id)
    for to_email, label, is_admin in _build_recipients(order):
        try:
            _send_email(to_email, order, label, is_admin)
        except Exception:
            logger.exception("Failed to send order confirmation email for order %s to %s", order_id, to_email)


def send_order_cancellation_emails(order_id: int) -> None:
    if not getattr(settings, "EMAIL_HOST", "") or not getattr(settings, "DEFAULT_FROM_EMAIL", ""):
        logger.warning("Email settings are incomplete; skipping order cancellation for order %s", order_id)
        return

    order = Order.objects.prefetch_related("items__product").get(pk=order_id)
    for to_email, label, is_admin in _build_recipients(order):
        try:
            _send_cancellation_email(to_email, order, label, is_admin)
        except Exception:
            logger.exception("Failed to send order cancellation email for order %s to %s", order_id, to_email)
