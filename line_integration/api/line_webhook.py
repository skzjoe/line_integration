import base64
import hashlib
import hmac
import json
import re

import frappe
from frappe.utils import now_datetime

from line_integration.utils.line_client import (
    ensure_profile,
    get_settings,
    reply_message,
)

REGISTER_PROMPT = "สวัสดีค่า! เพื่อทำการลงทะเบียน กรุณาส่งชื่อที่ต้องการใช้งานมาให้เราค่ะ"
ASK_PHONE_PROMPT = "ขอบคุณค่า! ตอนนี้กรุณาส่งหมายเลขโทรศัพท์ 10 หลักของคุณ (ไม่มีขีดหรือตัวอักษรอื่นๆ) มาให้เราค่ะ"
ALREADY_REGISTERED_MSG = "สวัสดีค่าคุณ {name} คุณได้ทำการสมัครสมาชิกไปเรียบร้อยแล้ว"
PHONE_REGEX = re.compile(r"^\d{10}$")


@frappe.whitelist(allow_guest=True)
def line_webhook():
    """LINE webhook endpoint."""
    raw_body = frappe.request.get_data() or b""
    logger = frappe.logger("line_webhook")
    settings = get_settings()
    signature = (frappe.get_request_header("X-Line-Signature") or "").strip()

    logger.info(
        {
            "event": "line_webhook_received",
            "has_signature": bool(signature),
            "body_len": len(raw_body),
            "path": frappe.request.path,
        }
    )

    # password field; use decrypted secret (frappe handles password field decryption)
    channel_secret = settings.get_password("channel_secret")
    if not signature or not channel_secret:
        logger.warning("Missing signature or channel secret")
        frappe.local.response.http_status_code = 400
        return "Missing signature or channel secret"

    digest = hmac.new(channel_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected_signature = base64.b64encode(digest).decode().strip()

    # Debug log to help diagnose signature mismatches (does not log secrets)
    logger.info(
        {
            "event": "line_webhook_signature_check",
            "provided_signature": signature,
            "expected_signature": expected_signature,
            "channel_secret_len": len(channel_secret) if channel_secret else 0,
            "body_len": len(raw_body),
        }
    )

    if not hmac.compare_digest(expected_signature, signature):
        logger.warning("Invalid signature")
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
    state = get_state(user_id)

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
            if state and state.get("stage") == "awaiting_name":
                save_state(user_id, {"stage": "awaiting_phone", "name": text})
                reply_message(event.get("replyToken"), ASK_PHONE_PROMPT)
                return
            if state and state.get("stage") == "awaiting_phone":
                if PHONE_REGEX.match(text):
                    register_customer(
                        profile_doc, state.get("name", "").strip(), text, event.get("replyToken")
                    )
                    clear_state(user_id)
                else:
                    reply_message(
                        event.get("replyToken"),
                        "กรุณาส่งหมายเลขโทรศัพท์ 10 หลักของคุณ (ไม่มีขีดหรือตัวอักษรอื่นๆ).",
                    )
                return
            if lower == "register":
                if profile_doc.customer:
                    reply_message(
                        event.get("replyToken"),
                        ALREADY_REGISTERED_MSG.format(name=profile_doc.customer),
                    )
                    return
                clear_state(user_id)
                save_state(user_id, {"stage": "awaiting_name"})
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


def register_customer(profile_doc, full_name, phone_number, reply_token):
    if not full_name:
        reply_message(reply_token, "Please tell us your name to continue.")
        return
    if not phone_number or not PHONE_REGEX.match(phone_number):
        reply_message(reply_token, "Invalid phone number. Please send 10 digits.")
        return

    existing_customer = frappe.db.get_value(
        "Customer", {"mobile_no": phone_number}, "name"
    )

    try:
        if existing_customer:
            profile_doc.customer = existing_customer
            profile_doc.status = "Active"
            profile_doc.last_seen = now_datetime()
            profile_doc.save(ignore_permissions=True)
            reply_message(
                reply_token,
                ALREADY_REGISTERED_MSG.format(name=existing_customer),
            )
            return

        customer_group = (
            frappe.db.get_default("customer_group")
            or frappe.db.get_default("Customer Group")
            or frappe.db.get_single_value("Selling Settings", "customer_group")
            or "All Customer Groups"
        )
        territory = (
            frappe.db.get_default("territory")
            or frappe.db.get_default("Territory")
            or frappe.db.get_single_value("Selling Settings", "territory")
            or "All Territories"
        )

        customer = frappe.get_doc(
            {
                "doctype": "Customer",
                "customer_name": full_name,
                "customer_type": "Individual",
                "customer_group": customer_group,
                "territory": territory,
                "mobile_no": phone_number,
            }
        ).insert(ignore_permissions=True)

        frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": full_name,
                "mobile_no": phone_number,
                "phone": phone_number,
                "links": [
                    {
                        "link_doctype": "Customer",
                        "link_name": customer.name,
                    }
                ],
            }
        ).insert(ignore_permissions=True)

        profile_doc.customer = customer.name
        profile_doc.status = "Active"
        profile_doc.last_seen = now_datetime()
        profile_doc.save(ignore_permissions=True)

        reply_message(
            reply_token,
            f"Created customer {customer.name} and linked to your LINE. Thank you!",
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Registration Error")
        reply_message(
            reply_token,
            "Sorry, we could not complete your registration right now. Please try again later.",
        )


def cache_key(user_id):
    return f"line_registration_state:{user_id}"


def get_state(user_id):
    return frappe.cache().get_value(cache_key(user_id)) or {}


def save_state(user_id, state):
    frappe.cache().set_value(cache_key(user_id), state, expires_in_sec=3600)


def clear_state(user_id):
    frappe.cache().delete_value(cache_key(user_id))


@frappe.whitelist(allow_guest=True)
def ping():
    """Simple health check to confirm module is loaded."""
    return "pong"
