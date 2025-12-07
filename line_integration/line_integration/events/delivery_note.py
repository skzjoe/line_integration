import frappe

from line_integration.utils.line_client import push_message


def send_line_notification(doc, method=None):
    try:
        if not doc.customer:
            return
        user_id = frappe.db.get_value(
            "LINE Profile", {"customer": doc.customer}, "line_user_id"
        )
        if not user_id:
            user_id = frappe.db.get_value(
                "Customer", doc.customer, "custom_line_user_id"
            )
        if user_id:
            push_message(user_id, f"Your order {doc.name} has been submitted.")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Delivery Note LINE Notification")
