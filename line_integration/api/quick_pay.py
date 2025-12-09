import frappe
from frappe import _

from line_integration.utils.line_client import get_settings, push_message
from line_integration.api.line_webhook import resolve_public_image_url, format_qty


@frappe.whitelist()
def quick_pay_sales_order(sales_order: str):
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

    si = _make_sales_invoice(so)
    pe = _make_payment_entry(si, mop)

    return _("Created Sales Invoice {0} and Payment Entry {1}").format(si.name, pe.name)


def _make_sales_invoice(so):
    from erpnext.selling.doctype.sales_order.sales_order import make_sales_invoice

    si = make_sales_invoice(so.name)
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
