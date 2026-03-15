from __future__ import annotations

from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .models import Order

COMPANY_NAME = "REVE LIVING"
DEFAULT_DELIVERY_TIMESCALE = "Standard Delivery"
UPPER_FLOORS_NOTE = "Upper floors: GBP 10 per floor, payable to driver."


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


def _draw_text(c: canvas.Canvas, x: float, y: float, text: str, size: int = 9, font: str = "Helvetica") -> float:
    c.setFont(font, size)
    c.drawString(x, y, text)
    return y


def _draw_heading(c: canvas.Canvas, x: float, y: float, text: str) -> float:
    c.setFont("Helvetica-BoldOblique", 11)
    c.drawString(x, y, text)
    return y - 14


def _draw_label_value(c: canvas.Canvas, x: float, y: float, label: str, value: str) -> float:
    c.setFont("Helvetica", 9)
    c.drawString(x, y, f"{label}{value}")
    return y - 11


def _draw_products_table(c: canvas.Canvas, x: float, y: float, order: Order) -> float:
    col_widths = [310, 120]
    row_height = 16
    table_width = sum(col_widths)

    c.setFont("Helvetica", 8)
    c.setFillGray(0.92)
    c.rect(x, y - row_height, table_width, row_height, stroke=1, fill=1)
    c.setFillGray(0)

    c.drawString(x + 6, y - 11, "Product Name")
    c.drawString(x + col_widths[0] + 6, y - 11, "Quantity")

    current_y = y - row_height
    items = list(order.items.select_related("product").all())

    for item in items or [None]:
        current_y -= row_height
        c.rect(x, current_y, table_width, row_height, stroke=1, fill=0)
        c.line(x + col_widths[0], current_y, x + col_widths[0], current_y + row_height)
        product_name = (
            item.product.name if item and item.product else f"Product #{item.product_id}" if item else "No items found"
        )
        quantity = str(item.quantity) if item else "-"
        c.drawString(x + 6, current_y + 5, product_name[:58])
        c.drawString(x + col_widths[0] + 6, current_y + 5, quantity)

    return current_y - 16


def build_delivery_note_pdf(order: Order) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    _, height = A4
    left = 36
    top = height - 40

    note_parts = _extract_order_note_parts(order)
    customer_name = f"{order.first_name} {order.last_name}".strip()

    y = top
    y = _draw_text(c, left, y, f"{COMPANY_NAME} - DELIVERY NOTE", size=11, font="Helvetica-Bold")
    y -= 16

    y = _draw_heading(c, left, y, "Order Information")
    y = _draw_label_value(c, left, y, "Order Number: ", str(order.id))
    y = _draw_label_value(c, left, y, "Order Date: ", order.created_at.strftime("%d %B %Y"))
    y = _draw_label_value(c, left, y, "Delivery Timescale: ", DEFAULT_DELIVERY_TIMESCALE)
    y -= 2

    y = _draw_heading(c, left, y, "Customer Details")
    y = _draw_label_value(c, left, y, "Customer Name: ", customer_name)
    y = _draw_label_value(c, left, y, "Phone Number: ", order.phone)
    y = _draw_label_value(c, left, y, "Alternative Phone: ", note_parts["alt_phone"] or "Not given")
    y = _draw_label_value(c, left, y, "Email Address: ", order.email)
    y -= 2

    y = _draw_heading(c, left, y, "Delivery Address")
    y = _draw_text(c, left, y, order.address)
    y -= 10
    y = _draw_text(c, left, y, order.city)
    y -= 10
    y = _draw_text(c, left, y, order.postal_code)
    y -= 10
    y = _draw_text(c, left, y, note_parts["floor_number"] or "Not given")
    y -= 10
    y = _draw_text(c, left, y, UPPER_FLOORS_NOTE)
    y -= 10

    y = _draw_heading(c, left, y, "Delivery / Order Notes")
    if note_parts["customer_note"]:
        for note_line in note_parts["customer_note"].splitlines():
            y = _draw_text(c, left, y, note_line)
            y -= 2
    else:
        y = _draw_text(c, left, y, "Not given")
    y -= 10

    y = _draw_heading(c, left, y, "Products Ordered")
    y = _draw_products_table(c, left + 60, y, order)

    y = _draw_heading(c, left, y, "Payment Information")
    y = _draw_label_value(c, left, y, "Payment Method: ", order.payment_method or "")
    y = _draw_label_value(c, left, y, "Total Amount: ", f"GBP {float(order.total_amount):.2f}")
    y -= 8

    y = _draw_heading(c, left, y, "Delivery Confirmation")
    y = _draw_label_value(c, left, y, "Customer Name: ", customer_name)
    y -= 3
    y = _draw_text(c, left, y, "Customer Signature: __________________")
    y -= 16
    y = _draw_text(c, left, y, "Date Received: __________________")
    y -= 16
    y = _draw_text(c, left, y, "Driver Name: __________________")
    y -= 16
    _draw_text(c, left, y, "Driver Signature: __________________")

    c.save()
    return buffer.getvalue()
