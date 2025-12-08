import json

import requests
import frappe
from frappe.utils import now_datetime
from frappe.utils.password import get_decrypted_password


def get_settings():
    return frappe.get_single("LINE Settings")


def _headers(token):
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }


def reply_message(reply_token, content):
    logger = frappe.logger("line_webhook")
    if not reply_token:
        logger.info({"event": "line_reply_skip", "reason": "missing_reply_token"})
        return False
    settings = get_settings()
    if not settings.enabled:
        logger.info({"event": "line_reply_skip", "reason": "settings_disabled"})
        return False
    access_token = get_decrypted_password("LINE Settings", "LINE Settings", "channel_access_token") or ""
    if not access_token:
        logger.warning({"event": "line_reply_skip", "reason": "missing_access_token"})
        return False
    url = "https://api.line.me/v2/bot/message/reply"
    messages = []
    if isinstance(content, str):
        messages = [{"type": "text", "text": content}]
    elif isinstance(content, dict):
        messages = [content]
    elif isinstance(content, (list, tuple)):
        messages = list(content)
    else:
        logger.warning({"event": "line_reply_skip", "reason": "unsupported_content_type"})
        return False
    payload = {"replyToken": reply_token, "messages": messages}
    try:
        resp = requests.post(
            url,
            data=json.dumps(payload),
            headers=_headers(access_token),
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error(
                {
                    "event": "line_reply_failed",
                    "status": resp.status_code,
                    "body": resp.text,
                    "payload_messages": messages,
                }
            )
            try:
                frappe.log_error(
                    {
                        "event": "line_reply_failed",
                        "status": resp.status_code,
                        "body": resp.text,
                        "payload_messages": messages,
                    },
                    "LINE Reply Error",
                )
            except Exception:
                logger.warning({"event": "line_reply_log_error_failed"})
            return False
        else:
            logger.info(
                {
                    "event": "line_reply_success",
                    "status": resp.status_code,
                    "payload_messages": messages,
                }
            )
            return True
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Reply Error")
        return False


def push_message(user_id, text):
    logger = frappe.logger("line_webhook")
    if not user_id:
        logger.info({"event": "line_push_skip", "reason": "missing_user_id"})
        return
    settings = get_settings()
    if not settings.enabled:
        logger.info({"event": "line_push_skip", "reason": "settings_disabled"})
        return
    access_token = get_decrypted_password("LINE Settings", "LINE Settings", "channel_access_token") or ""
    if not access_token:
        logger.warning({"event": "line_push_skip", "reason": "missing_access_token"})
        return
    url = "https://api.line.me/v2/bot/message/push"
    payload = {"to": user_id, "messages": [{"type": "text", "text": text}]}
    try:
        resp = requests.post(
            url,
            data=json.dumps(payload),
            headers=_headers(access_token),
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error(
                {
                    "event": "line_push_failed",
                    "status": resp.status_code,
                    "body": resp.text,
                    "payload_messages": payload.get("messages"),
                }
            )
            try:
                frappe.log_error(
                    {
                        "event": "line_push_failed",
                        "status": resp.status_code,
                        "body": resp.text,
                        "payload_messages": payload.get("messages"),
                    },
                    "LINE Push Error",
                )
            except Exception:
                logger.warning({"event": "line_push_log_error_failed"})
        else:
            logger.info(
                {
                    "event": "line_push_success",
                    "status": resp.status_code,
                    "payload_messages": payload.get("messages"),
                }
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
