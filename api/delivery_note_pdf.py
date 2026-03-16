from __future__ import annotations

from io import BytesIO
import re

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

from .models import Order

COMPANY_NAME = "REVE LIVING"
DEFAULT_DELIVERY_TIMESCALE = "Standard Delivery"
UPPER_FLOORS_NOTE = "Upper floors: GBP 10 per floor, payable to driver."
PAGE_MARGIN = 36
BOTTOM_MARGIN = 42
TABLE_FONT = "Helvetica"
TABLE_FONT_BOLD = "Helvetica-Bold"
TABLE_FONT_SIZE = 9
TABLE_LEADING = 12
TABLE_CELL_PADDING = 6


def _extract_order_note_parts(order: Order) -> dict[str, str]:
    alt_phone = order.alternative_phone or ""
    floor_number = order.floor_number or ""
    customer_note_lines: list[str] = []

    for raw_line in (order.special_notes or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue

        lower = line.lower()
        if not alt_phone and lower.startswith("alternative phone number:"):
            alt_phone = line.split(":", 1)[1].strip()
            continue
        if not alt_phone and lower.startswith("alternative phone:"):
            alt_phone = line.split(":", 1)[1].strip()
            continue
        if not floor_number and lower.startswith("floor number:"):
            floor_number = line.split(":", 1)[1].strip()
            continue

        customer_note_lines.append(line)

    return {
        "alt_phone": alt_phone,
        "floor_number": floor_number,
        "customer_note": "\n".join(customer_note_lines).strip(),
    }


def _format_money(value) -> str:
    try:
        return f"GBP {float(value):.2f}"
    except (TypeError, ValueError):
        return f"GBP {value}"


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


def _payment_label(method: str) -> str:
    return {
        "cod": "Cash on Delivery",
        "card": "Card",
        "paypal": "PayPal",
    }.get((method or "").lower(), method or "Not provided")


def _draw_page_header(c: canvas.Canvas, title: str) -> float:
    _, page_height = A4
    y = page_height - PAGE_MARGIN
    c.setFont(TABLE_FONT_BOLD, 12)
    c.drawString(PAGE_MARGIN, y, title)
    return y - 22


def _ensure_space(c: canvas.Canvas, y: float, required_height: float, title: str) -> float:
    if y - required_height >= BOTTOM_MARGIN:
        return y
    c.showPage()
    return _draw_page_header(c, title)


def _split_cell_lines(value: str, width: float, font_name: str = TABLE_FONT) -> list[str]:
    raw_text = str(value or "").strip()
    parts = [part.strip() for part in raw_text.splitlines() if part.strip()]
    if not parts:
        parts = ["-"]

    lines: list[str] = []
    for part in parts:
        wrapped = simpleSplit(part, font_name, TABLE_FONT_SIZE, max(width, 40))
        lines.extend(wrapped or [""])
    return lines or ["-"]


def _draw_table_row(
    c: canvas.Canvas,
    x: float,
    y: float,
    label: str,
    value: str,
    label_width: float,
    value_width: float,
) -> float:
    label_lines = _split_cell_lines(label, label_width - (TABLE_CELL_PADDING * 2), TABLE_FONT_BOLD)
    value_lines = _split_cell_lines(value, value_width - (TABLE_CELL_PADDING * 2), TABLE_FONT)
    line_count = max(len(label_lines), len(value_lines))
    row_height = max(24, (line_count * TABLE_LEADING) + (TABLE_CELL_PADDING * 2))

    c.setFillGray(0.97)
    c.rect(x, y - row_height, label_width, row_height, stroke=1, fill=1)
    c.setFillGray(1)
    c.rect(x + label_width, y - row_height, value_width, row_height, stroke=1, fill=1)
    c.setFillGray(0)

    text_y = y - TABLE_CELL_PADDING - TABLE_FONT_SIZE

    c.setFont(TABLE_FONT_BOLD, TABLE_FONT_SIZE)
    for line in label_lines:
        c.drawString(x + TABLE_CELL_PADDING, text_y, line)
        text_y -= TABLE_LEADING

    text_y = y - TABLE_CELL_PADDING - TABLE_FONT_SIZE
    c.setFont(TABLE_FONT, TABLE_FONT_SIZE)
    for line in value_lines:
        c.drawString(x + label_width + TABLE_CELL_PADDING, text_y, line)
        text_y -= TABLE_LEADING

    return y - row_height


def _draw_table_rows(
    c: canvas.Canvas,
    y: float,
    rows: list[tuple[str, str]],
    title: str,
    label_width: float = 185,
) -> float:
    page_width, _ = A4
    x = PAGE_MARGIN
    total_width = page_width - (PAGE_MARGIN * 2)
    value_width = total_width - label_width

    for label, value in rows:
        label_lines = _split_cell_lines(label, label_width - (TABLE_CELL_PADDING * 2), TABLE_FONT_BOLD)
        value_lines = _split_cell_lines(value, value_width - (TABLE_CELL_PADDING * 2), TABLE_FONT)
        line_count = max(len(label_lines), len(value_lines))
        row_height = max(24, (line_count * TABLE_LEADING) + (TABLE_CELL_PADDING * 2))
        y = _ensure_space(c, y, row_height, title)
        y = _draw_table_row(c, x, y, label, value, label_width, value_width)
    return y - 14


def _order_item_summary(order: Order) -> str:
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
                    _append_unique(parts, seen, f"Extras: {_format_money(item.extras_total)}")
            except (TypeError, ValueError):
                pass
        lines.append(" | ".join(_sort_order_parts(parts)))
    return "\n\n".join(lines) if lines else "No products found."


def build_delivery_note_pdf(order: Order) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    note_parts = _extract_order_note_parts(order)
    customer_name = f"{order.first_name} {order.last_name}".strip()
    title = f"{COMPANY_NAME} - DELIVERY NOTE"

    y = _draw_page_header(c, title)

    rows = [
        ("Order Number", str(order.id)),
        ("Order Date", order.created_at.strftime("%d %B %Y")),
        ("Delivery Timescale", DEFAULT_DELIVERY_TIMESCALE),
        ("Customer Name", customer_name or "-"),
        ("Phone Number", order.phone or "-"),
        ("Alternative Phone Number", note_parts["alt_phone"] or "Not given"),
        ("Email Address", order.email or "-"),
        ("Street Address", "\n".join(filter(None, [order.address, order.city, order.postal_code])) or "-"),
        ("Customer Floor Number", note_parts["floor_number"] or "Not given"),
        ("Delivery Note", UPPER_FLOORS_NOTE),
        ("Special Notes", note_parts["customer_note"] or "Not given"),
        ("Product Description", _order_item_summary(order)),
        ("Delivery Charges", _format_money(order.delivery_charges)),
        ("Payment Method", _payment_label(order.payment_method)),
        ("Total Amount", _format_money(order.total_amount)),
    ]
    y = _draw_table_rows(c, y, rows, title)

    y = _ensure_space(c, y, 110, title)
    signature_rows = [
        ("Customer Name", customer_name or "-"),
        ("Customer Signature", "__________________"),
        ("Date Received", "__________________"),
        ("Driver Name", "__________________"),
        ("Driver Signature", "__________________"),
    ]
    _draw_table_rows(c, y, signature_rows, title)

    c.save()
    return buffer.getvalue()
