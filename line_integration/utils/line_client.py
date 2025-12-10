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
        return False
    settings = get_settings()
    if not settings.enabled:
        logger.info({"event": "line_push_skip", "reason": "settings_disabled"})
        return False
    access_token = get_decrypted_password("LINE Settings", "LINE Settings", "channel_access_token") or ""
    if not access_token:
        logger.warning({"event": "line_push_skip", "reason": "missing_access_token"})
        return False
    url = "https://api.line.me/v2/bot/message/push"
    messages = []
    if isinstance(text, str):
        messages = [{"type": "text", "text": text}]
    elif isinstance(text, dict):
        messages = [text]
    elif isinstance(text, (list, tuple)):
        messages = list(text)
    else:
        logger.warning({"event": "line_push_skip", "reason": "unsupported_content_type"})
        return False
    payload = {"to": user_id, "messages": messages}
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
                    "payload_messages": messages,
                }
            )
            try:
                frappe.log_error(
                    {
                        "event": "line_push_failed",
                        "status": resp.status_code,
                        "body": resp.text,
                        "payload_messages": messages,
                    },
                    "LINE Push Error",
                )
            except Exception:
                logger.warning({"event": "line_push_log_error_failed"})
            return False
        else:
            logger.info(
                {
                    "event": "line_push_success",
                    "status": resp.status_code,
                    "payload_messages": messages,
                }
            )
            return True
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Push Error")
        return False


def ensure_profile(user_id, event=None):
    def _truncate(value, max_length):
        """Trim value to the allowed length of the LINE Profile fields."""
        if value and max_length and len(value) > max_length:
            return value[:max_length]
        return value

    profile_name = frappe.db.get_value("LINE Profile", {"line_user_id": user_id})
    if profile_name:
        doc = frappe.get_doc("LINE Profile", profile_name)
    else:
        doc = frappe.new_doc("LINE Profile")
        doc.line_user_id = user_id
        doc.status = "Active"

    source = (event or {}).get("source") or {}
    display_name = source.get("displayName")
    picture_url = source.get("pictureUrl")

    # If display_name not provided in event, fetch from LINE profile API
    if not display_name:
        try:
            profile = fetch_line_profile(user_id)
            display_name = profile.get("displayName") or display_name
            picture_url = profile.get("pictureUrl") or picture_url
        except Exception:
            pass

    max_display_len = (doc.meta.get_field("display_name") or {}).get("length", 0)
    max_picture_len = (doc.meta.get_field("picture_url") or {}).get("length", 0)

    doc.display_name = _truncate(display_name or doc.display_name, max_display_len or None)
    doc.picture_url = _truncate(picture_url or doc.picture_url, max_picture_len or None)
    doc.last_seen = now_datetime()
    if event:
        doc.last_event = json.dumps(event)
    doc.save(ignore_permissions=True)
    return doc


def fetch_line_profile(user_id):
    """Fetch LINE profile data for a given user_id."""
    if not user_id:
        return {}
    settings = get_settings()
    access_token = get_decrypted_password("LINE Settings", "LINE Settings", "channel_access_token") or ""
    if not access_token:
        return {}
    url = f"https://api.line.me/v2/bot/profile/{user_id}"
    resp = requests.get(url, headers=_headers(access_token), timeout=10)
    if resp.status_code != 200:
        return {}
    try:
        return resp.json() or {}
    except Exception:
        return {}
