import json

import requests
import frappe
from frappe.utils import now_datetime


def get_settings():
    return frappe.get_single("LINE Settings")


def _headers(token):
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def reply_message(reply_token, text):
    if not reply_token:
        return
    settings = get_settings()
    if not settings.enabled or not settings.channel_access_token:
        return
    url = "https://api.line.me/v2/bot/message/reply"
    payload = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
    try:
        requests.post(
            url,
            data=json.dumps(payload),
            headers=_headers(settings.channel_access_token),
            timeout=10,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Reply Error")


def push_message(user_id, text):
    if not user_id:
        return
    settings = get_settings()
    if not settings.enabled or not settings.channel_access_token:
        return
    url = "https://api.line.me/v2/bot/message/push"
    payload = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    try:
        requests.post(
            url,
            data=json.dumps(payload),
            headers=_headers(settings.channel_access_token),
            timeout=10,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Push Error")


def ensure_profile(user_id, event=None):
    profile_name = frappe.db.get_value("LINE Profile", {"line_user_id": user_id})
    if profile_name:
        doc = frappe.get_doc("LINE Profile", profile_name)
    else:
        doc = frappe.new_doc("LINE Profile")
        doc.line_user_id = user_id
        doc.status = "Active"

    source = (event or {}).get("source") or {}
    doc.display_name = source.get("displayName") or doc.display_name
    doc.picture_url = source.get("pictureUrl") or doc.picture_url
    doc.last_seen = now_datetime()
    if event:
        doc.last_event = json.dumps(event)
    doc.save(ignore_permissions=True)
    return doc
