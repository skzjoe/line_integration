import base64
import hashlib
import hmac
import json
import re

import frappe
from frappe.utils import now_datetime

from line_integration.utils.line_client import ensure_profile, get_settings, reply_message

REGISTER_PROMPT = "Please provide your 10-digit phone number to link your account."
PHONE_REGEX = re.compile(r"^\\d{10}$")


@frappe.whitelist(allow_guest=True)
def line_webhook():
    """LINE webhook endpoint."""
    raw_body = frappe.request.get_data() or b""
    settings = get_settings()
    signature = frappe.get_request_header("X-Line-Signature")

    if not signature or not settings.channel_secret:
        frappe.local.response.http_status_code = 400
        return "Missing signature or channel secret"

    digest = hmac.new(
        settings.channel_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).digest()
    expected_signature = base64.b64encode(digest).decode()

    if not hmac.compare_digest(expected_signature, signature):
        frappe.local.response.http_status_code = 400
        return "Invalid signature"

    payload = json.loads(raw_body.decode("utf-8") or "{}")
    events = payload.get("events", []) or []

    for event in events:
        try:
            handle_event(event)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "LINE Webhook Error")

    return "OK"


def handle_event(event):
    event_type = event.get("type")
    source = event.get("source") or {}
    user_id = source.get("userId")
    if not user_id:
        return

    profile_doc = ensure_profile(user_id, event)

    if event_type == "unfollow":
        if profile_doc.status != "Blocked":
            profile_doc.status = "Blocked"
            profile_doc.last_event = json.dumps(event)
            profile_doc.save(ignore_permissions=True)
        return

    if event_type == "follow":
        profile_doc.status = "Active"
        profile_doc.last_event = json.dumps(event)
        profile_doc.save(ignore_permissions=True)
        reply_message(event.get("replyToken"), "Thanks for following us!")
        return

    if event_type == "message":
        message = event.get("message") or {}
        if message.get("type") == "text":
            text = (message.get("text") or "").strip()
            lower = text.lower()
            if lower == "register":
                reply_message(event.get("replyToken"), REGISTER_PROMPT)
                return
            if PHONE_REGEX.match(text):
                link_customer(profile_doc, text, event.get("replyToken"))
                return
        profile_doc.last_event = json.dumps(event)
        profile_doc.last_seen = now_datetime()
        profile_doc.save(ignore_permissions=True)


def link_customer(profile_doc, phone_number, reply_token):
    customer_name = frappe.db.get_value(
        "Customer", {"mobile_no": phone_number}, "name"
    )
    if customer_name:
        profile_doc.customer = customer_name
        profile_doc.status = "Active"
        profile_doc.last_seen = now_datetime()
        profile_doc.save(ignore_permissions=True)
        reply_message(reply_token, f"Linked to customer {customer_name}. Thank you!")
    else:
        reply_message(reply_token, "Customer not found. Please contact support.")
