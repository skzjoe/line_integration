import frappe
from frappe import _

from line_integration.utils.line_client import get_settings, push_message
from line_integration.api.line_webhook import resolve_public_image_url, format_qty
from frappe.utils.jinja import render_template


@frappe.whitelist()
def quick_pay_sales_order(sales_order: str, points_to_redeem: float = 0):
    """Create Sales Invoice + Payment Entry for a submitted Sales Order."""
    if not sales_order:
        frappe.throw(_("Sales Order is required"))

    settings = get_settings()
    mop = settings.quick_pay_mode_of_payment
    if not mop:
        frappe.throw(_("Please set Quick Pay Mode of Payment in LINE Settings"))

    so = frappe.get_doc("Sales Order", sales_order)
    if so.docstatus != 1:
        frappe.throw(_("Sales Order must be Submitted"))

    si = _make_sales_invoice(so, points_to_redeem, settings)
    pe = _make_payment_entry(si, mop)

    msg = _("Created Sales Invoice {0} and Payment Entry {1}").format(si.name, pe.name)
    if points_to_redeem:
        msg += _("<br>Redeemed points: {0}").format(points_to_redeem)
    return msg


def _make_sales_invoice(so, points_to_redeem=0, settings=None):
    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

    si = make_sales_invoice(so.name)
    redeem_amount = 0
    if points_to_redeem and settings:
        try:
            lp_details = _get_loyalty_details(so.customer, settings)
            value_per_point = lp_details.get("conversion_factor") or 0
            redeem_amount = float(points_to_redeem) * float(value_per_point or 0)
        except Exception:
            redeem_amount = 0
    if redeem_amount > 0:
        si.apply_discount_on = "Grand Total"
        si.discount_amount = redeem_amount
        si.loyalty_points = points_to_redeem

    si.flags.ignore_permissions = True
    si.insert(ignore_permissions=True)
    si.submit()
    return si


def _make_payment_entry(si, mode_of_payment):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    pe = get_payment_entry(si.doctype, si.name)
    pe.mode_of_payment = mode_of_payment
    # Ensure amounts align with invoice outstanding
    pe.paid_amount = pe.received_amount = si.outstanding_amount
    pe.flags.ignore_permissions = True
    pe.insert(ignore_permissions=True)
    pe.submit()
    return pe


@frappe.whitelist()
def request_payment(sales_order: str):
    """Send payment request with total amount and QR code from settings."""
    if not sales_order:
        frappe.throw(_("Sales Order is required"))

    settings = get_settings()
    message = settings.request_payment_message or _("กรุณาชำระเงินตามยอดที่แจ้งและส่งสลิปยืนยันค่ะ")
    qr_image = settings.request_payment_qr
    qr_url = resolve_public_image_url(qr_image)

    so = frappe.get_doc("Sales Order", sales_order)
    if so.docstatus != 1:
        frappe.throw(_("Sales Order must be Submitted"))

    total_text = frappe.utils.fmt_money(so.grand_total, currency=so.currency)
    total_qty = sum((row.qty or 0) for row in (so.items or []))

    lines = [
        message,
        f"หมายเลขออเดอร์ : {so.name}",
    ]
    for row in so.items or []:
        lines.append(f"{row.item_name or row.item_code} {format_qty(row.qty)} ขวด")
    lines.append(f"รวม {format_qty(total_qty)} ขวด")
    lines.append(f"ยอดรวม {total_text}")
    text = "\n".join(lines)

    if not qr_url:
        frappe.throw(_("Please set a public QR Code image in LINE Settings"))

    # Push to all LINE Profiles linked to the Customer
    profiles = frappe.get_all(
        "LINE Profile",
        filters={"customer": so.customer, "status": "Active"},
        fields=["line_user_id"],
    )
    if not profiles:
        return _("No LINE Profile linked to Customer {0}, nothing sent.").format(so.customer)

    messages = [{"type": "text", "text": text}, {"type": "image", "originalContentUrl": qr_url, "previewImageUrl": qr_url}]
    sent_count = 0
    for p in profiles:
        if push_message(p.line_user_id, messages):
            sent_count += 1

    return _("Sent payment request to {0} LINE user(s).").format(sent_count)


@frappe.whitelist()
def get_loyalty_balance(sales_order: str):
    """Return available points/value for the customer of this Sales Order."""
    if not sales_order:
        frappe.throw(_("Sales Order is required"))
    so = frappe.get_doc("Sales Order", sales_order)
    settings = get_settings()
    lp_details = _get_loyalty_details(so.customer, settings)
    points = lp_details.get("loyalty_points", 0) or 0
    value_per_point = lp_details.get("conversion_factor", 0) or 0
    max_amount = points * value_per_point
    return {
        "points": points,
        "value_per_point": value_per_point,
        "max_amount": max_amount,
    }


def _get_loyalty_details(customer, settings):
    try:
        from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
            get_loyalty_program_details_with_points,
        )

        return get_loyalty_program_details_with_points(
            customer=customer,
            loyalty_program=settings.loyalty_program,
        ) or {}
    except Exception:
        return {}


@frappe.whitelist()
def print_bag_label(sales_order: str):
    """Render a simple printable label for the sales order."""
    if not sales_order:
        frappe.throw(_("Sales Order is required"))
    so = frappe.get_doc("Sales Order", sales_order)
    if so.docstatus != 1:
        frappe.throw(_("Sales Order must be Submitted"))
    context = {
        "customer_name": so.customer_name or so.customer,
        "items": [
            {"item_name": row.item_name or row.item_code, "qty": format_qty(row.qty)}
            for row in so.items
        ],
        "total": frappe.utils.fmt_money(so.grand_total, currency=so.currency),
    }
    html = render_template("line_integration/api/print_label.html", context)
    frappe.response["type"] = "binary"
    frappe.response["filename"] = f"BagLabel-{so.name}.html"
    frappe.response["filecontent"] = html
    frappe.response["display_content_as"] = "utf-8"


@frappe.whitelist()
def get_order_copy_text(sales_order: str):
    """Return plain text order summary for copying."""
    if not sales_order:
        frappe.throw(_("Sales Order is required"))
    so = frappe.get_doc("Sales Order", sales_order)
    lines = [so.customer_name or so.customer]
    for row in so.items:
        lines.append(f"{row.item_name or row.item_code} : {format_qty(row.qty)}")
    lines.append(f"{frappe.utils.fmt_money(so.grand_total, currency=so.currency)}")
    return "\n".join(lines)
