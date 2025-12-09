import base64
import hashlib
import hmac
import json
import re
# import urllib.parse

import frappe
from frappe.utils import add_days, fmt_money, get_url, now_datetime, today

from line_integration.utils.line_client import (
    ensure_profile,
    get_settings,
    reply_message,
)

# Fallback defaults; settings fields override these at runtime
DEFAULT_REGISTER_PROMPT = "สวัสดีค่า! เพื่อทำการลงทะเบียน กรุณาส่งชื่อที่ต้องการใช้งานมาให้เราค่ะ"
DEFAULT_ASK_PHONE_PROMPT = "ขอบคุณค่า! ตอนนี้กรุณาส่งหมายเลขโทรศัพท์ 10 หลักของคุณ (ไม่มีขีดหรือตัวอักษรอื่นๆ) มาให้เราค่ะ"
DEFAULT_ALREADY_REGISTERED_MSG = "สวัสดีค่าคุณ {name} คุณได้ทำการสมัครสมาชิกไปเรียบร้อยแล้ว"
DEFAULT_ORDER_REPLY = "แจ้งรายการสั่งซื้อหรือพิมพ์ชื่อเมนูที่ต้องการได้เลยค่ะ"
DEFAULT_LOYALTY_PROGRAM = "Wellie Point"
PHONE_REGEX = re.compile(r"^\d{10}$")
CONFIRM_KEYWORDS = {"confirm", "ยืนยัน", "ตกลง"}
CANCEL_KEYWORDS = {"cancel", "ยกเลิก"}
QTY_PATTERN = re.compile(
    r"^[\-\u2022\u2013\u2014]?\s*(?P<name>.+?)\s*จำนวน[:：]?\s*(?P<qty>[0-9]+(?:\.[0-9]+)?)\s*$",
    re.IGNORECASE,
)

KEYWORD_KIND_MAP = {
    "register": "Register",
    "points": "Points",
    "menu": "Menu",
    "order": "Order",
}


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
            handle_event(event, settings)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "LINE Webhook Error")

    return "OK"


def handle_event(event, settings):
    logger = frappe.logger("line_webhook")
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
            normalized = "".join(lower.split())

            # Pending order confirmation flow
            pending_order = get_order_state(user_id)
            if pending_order:
                if normalized in CONFIRM_KEYWORDS:
                    handled = finalize_order_from_state(profile_doc, pending_order, event.get("replyToken"), settings)
                    if handled:
                        clear_order_state(user_id)
                        return
                if normalized in CANCEL_KEYWORDS:
                    clear_order_state(user_id)
                    reply_message(event.get("replyToken"), "ยกเลิกออเดอร์เรียบร้อยค่ะ")
                    return
                # If other text while pending, remind
                reply_message(
                    event.get("replyToken"),
                    "กรุณาพิมพ์ \"ยืนยัน\" เพื่อยืนยันออเดอร์ หรือ \"ยกเลิก\" หากต้องการแก้ไขค่ะ",
                )
                return

            register_keywords = collect_keywords(settings, "register", ["register", "สมัครสมาชิก", "สมาชิก"])
            points_keywords = collect_keywords(settings, "points", ["ตรวจสอบpointคงเหลือ"])
            menu_keywords = collect_keywords(settings, "menu", ["เมนู"])
            order_keywords = collect_keywords(settings, "order", [settings.order_keyword or "สั่งออเดอร์"])
            register_prompt = settings.register_prompt or DEFAULT_REGISTER_PROMPT
            ask_phone_prompt = settings.ask_phone_prompt or DEFAULT_ASK_PHONE_PROMPT
            already_registered_msg = (
                settings.already_registered_message or DEFAULT_ALREADY_REGISTERED_MSG
            )
            order_reply_msg = settings.order_reply_message or DEFAULT_ORDER_REPLY

            keyword_log = {
                "event": "line_keyword_check",
                "user_id": user_id,
                "text": text,
                "normalized": normalized,
                "register_keywords": register_keywords,
                "points_keywords": points_keywords,
                "menu_keywords": menu_keywords,
                "order_keywords": order_keywords,
            }
            logger.info(keyword_log)

            first_line = (text.splitlines()[0] if text else "").strip()
            first_line_norm = "".join(first_line.lower().split())
            has_order_kw = first_line_norm in order_keywords["normalized"] or any(
                kw in normalized for kw in order_keywords["normalized"]
            )
            has_qty_lines = any(QTY_PATTERN.search((ln or "").strip()) for ln in (text or "").splitlines())

            if has_order_kw and has_qty_lines:
                if settings.require_order_confirmation:
                    handled = review_order_submission(profile_doc, text, event.get("replyToken"), settings, user_id)
                else:
                    handled = process_order_submission(profile_doc, text, event.get("replyToken"), settings)
                if handled:
                    return

            if normalized in points_keywords["normalized"]:
                reply_points(profile_doc, event.get("replyToken"))
                return
            if normalized in menu_keywords["normalized"]:
                reply_menu(event.get("replyToken"), settings)
                return
            if normalized in order_keywords["normalized"]:
                reply_order_form(event.get("replyToken"), settings)
                return
            if state and state.get("stage") == "awaiting_name":
                save_state(user_id, {"stage": "awaiting_phone", "name": text})
                reply_message(event.get("replyToken"), ask_phone_prompt)
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
            if normalized in register_keywords["normalized"]:
                if profile_doc.customer:
                    reply_registered_flex(profile_doc, event.get("replyToken"), settings)
                    return
                clear_state(user_id)
                save_state(user_id, {"stage": "awaiting_name"})
                reply_message(event.get("replyToken"), register_prompt)
                return
            if PHONE_REGEX.match(text):
                link_customer(profile_doc, text, event.get("replyToken"))
                return
        profile_doc.last_event = json.dumps(event)
        profile_doc.last_seen = now_datetime()
        profile_doc.save(ignore_permissions=True)


def link_customer(profile_doc, phone_number, reply_token):
    settings = get_settings()
    already_registered_msg = (
        settings.already_registered_message or DEFAULT_ALREADY_REGISTERED_MSG
    )
    customer_name = frappe.db.get_value(
        "Customer", {"mobile_no": phone_number}, "name"
    )
    if customer_name:
        profile_doc.customer = customer_name
        profile_doc.status = "Active"
        profile_doc.last_seen = now_datetime()
        profile_doc.save(ignore_permissions=True)
        reply_message(
            reply_token,
            already_registered_msg.format(name=customer_name),
        )
    else:
        reply_message(reply_token, "Customer not found. Please contact support.")


def reply_points(profile_doc, reply_token):
    if not profile_doc.customer:
        register_kw = first_keyword(
            collect_keywords(get_settings(), "register", ["สมัครสมาชิก"])["raw"],
            default="สมัครสมาชิก",
        )
        reply_message(
            reply_token,
            f"ยังไม่มีข้อมูลสมาชิก กรุณาพิมพ์ {register_kw} เพื่อเริ่มลงทะเบียนค่ะ",
        )
        return

    settings = get_settings()
    loyalty_program = settings.loyalty_program or DEFAULT_LOYALTY_PROGRAM

    try:
        try:
            from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
                get_loyalty_program_details_with_points,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "LINE Points Check Error: import erpnext.accounts.loyalty_program",
            )
            reply_message(
                reply_token,
                "ขออภัย ไม่สามารถตรวจสอบคะแนนได้ในขณะนี้ กรุณาลองใหม่อีกครั้งค่ะ",
            )
            return

        customer_name = profile_doc.customer
        display_name = (
            frappe.db.get_value("Customer", customer_name, "customer_name") or customer_name
        )
        lp_details = get_loyalty_program_details_with_points(
            customer=customer_name,
            loyalty_program=loyalty_program,
        )
        points = (lp_details or {}).get("loyalty_points", 0) or 0
        reply_message(
            reply_token,
            f"คุณ {display_name} มี {loyalty_program} คงเหลือ {points} แต้ม",
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Points Check Error")
        reply_message(
            reply_token,
            "ขออภัย ไม่สามารถตรวจสอบคะแนนได้ในขณะนี้ กรุณาลองใหม่อีกครั้งค่ะ",
        )


def reply_menu(reply_token, settings):
    logger = frappe.logger("line_webhook")
    try:
        items = fetch_menu_items(limit=10)
        menu_info = {"event": "line_menu_build", "items": len(items)}
        logger.info(menu_info)
        if not items:
            reply_message(reply_token, "ยังไม่มีเมนูที่พร้อมแสดงค่ะ")
            return

        summary_image = settings.menu_summary_image or (
            frappe.local.conf.get("line_menu_summary_image")
            if hasattr(frappe.local, "conf")
            else None
        )
        summary_image_url = resolve_public_image_url(summary_image, logger)

        bubbles = []
        if summary_image_url:
            bubbles.append(
                build_summary_bubble(
                    summary_image_url,
                    title="เมนูวันนี้",
                    subtitle="เลือกดูเมนูหรือคัดลอกฟอร์มสั่งออเดอร์แล้วส่งกลับได้เลยค่ะ",
                    aspect_ratio="1:1",
                )
            )

        for item in items:
            bubbles.append(build_item_bubble(item, logger))

        menu_carousel = {
            "type": "flex",
            "altText": "เมนู Wellie",
            "contents": {"type": "carousel", "contents": bubbles},
        }

        # Send as a single message (carousel) so userเลื่อนดูได้ในชุดเดียว
        sent = reply_message(reply_token, menu_carousel)
        logger.info(
            {
                "event": "line_menu_reply_attempt",
                "sent": bool(sent),
                "item_count": len(items),
                "has_summary": bool(summary_image_url),
            }
        )
        if not sent:
            frappe.log_error(
                {
                    "event": "line_menu_reply_failed",
                    "item_count": len(items),
                    "has_summary": bool(summary_image_url),
                },
                "LINE Menu Reply Failed",
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Menu Error")
        reply_message(reply_token, "ขออภัย ไม่สามารถแสดงเมนูได้ในขณะนี้ กรุณาลองใหม่อีกครั้งค่ะ")


def reply_order_form(reply_token, settings):
    """Send a single flex message with form template for user to fill quantities."""
    logger = frappe.logger("line_webhook")
    try:
        items = fetch_menu_items(limit=20)
        template_lines = ["สั่งออเดอร์"]
        for item in items or []:
            title = item.item_name or item.name
            template_lines.append(f"- {title} จำนวน: ")
        template_lines.append("หมายเหตุ: ")
        template_text = "\n".join(template_lines)

        summary_image = settings.menu_summary_image or (
            frappe.local.conf.get("line_menu_summary_image")
            if hasattr(frappe.local, "conf")
            else None
        )
        summary_image_url = resolve_public_image_url(summary_image, logger)

        body_contents = []
        if items:
            body_contents.append(
                {
                    "type": "text",
                    "text": "เมนู:",
                    "weight": "bold",
                    "size": "sm",
                    "margin": "md",
                }
            )
            for item in items:
                body_contents.append(
                    {
                        "type": "text",
                        "text": f"- {item.item_name or item.name}",
                        "size": "sm",
                        "color": "#444444",
                        "wrap": True,
                        "margin": "xs",
                    }
                )
        else:
            body_contents.append(
                {
                    "type": "text",
                    "text": "ยังไม่มีเมนูในระบบ",
                    "size": "sm",
                    "color": "#555555",
                    "wrap": True,
                    "margin": "md",
                }
                )

        flex_msg = build_summary_bubble(
            summary_image_url,
            title="ฟอร์มสั่งออเดอร์",
            subtitle=settings.order_form_subtitle or "กรอกจำนวนแล้วส่งกลับได้เลย",
            body_contents=body_contents,
            aspect_ratio="1:1",
        )
        flex_msg = {"type": "flex", "altText": "ฟอร์มสั่งออเดอร์", "contents": flex_msg}

        text_msg = (
            "คัดลอกข้อความนี้ แก้ไขจำนวน/หมายเหตุ แล้วส่งกลับได้เลย\n"
            f"{template_text}"
        )

        sent = reply_message(reply_token, [flex_msg, {"type": "text", "text": text_msg}])
        logger.info({"event": "line_order_form_reply_attempt", "sent": bool(sent), "item_count": len(items or [])})
        if not sent:
            frappe.log_error(
                {
                    "event": "line_order_form_reply_failed",
                    "item_count": len(items or []),
                },
                "LINE Order Form Reply Failed",
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Order Form Error")
        reply_message(reply_token, "ขออภัย ไม่สามารถส่งฟอร์มสั่งออเดอร์ได้ในขณะนี้ กรุณาลองใหม่อีกครั้งค่ะ")


def review_order_submission(profile_doc, text, reply_token, settings, user_id):
    """Parse order text and ask for confirmation before creating Sales Order."""
    logger = frappe.logger("line_webhook")
    if not settings.auto_create_sales_order or not settings.require_order_confirmation:
        return False
    if not profile_doc.customer:
        register_kw = first_keyword(
            collect_keywords(settings, "register", ["สมัครสมาชิก"])["raw"],
            default="สมัครสมาชิก",
        )
        reply_message(
            reply_token,
            f"ยังไม่พบข้อมูลสมาชิก กรุณาพิมพ์ {register_kw} เพื่อลงทะเบียนก่อนส่งออเดอร์นะคะ",
        )
        return True

    menu_items = fetch_menu_items(limit=200)
    item_map = {normalize_key(item.item_name or item.name): item for item in menu_items}

    orders, unknown, note = parse_orders_from_text(text, item_map)

    if not orders:
        reply_message(
            reply_token,
            "ยังไม่พบจำนวนในข้อความที่ส่งมา กรุณาคัดลอกฟอร์มจากปุ่มสั่งออเดอร์ แล้วเติมจำนวนก่อนส่งอีกครั้งนะคะ",
        )
        return True
    if unknown:
        reply_message(
            reply_token,
            "พบเมนูที่ไม่รู้จัก: " + ", ".join(unknown) + "\nกรุณาตรวจสอบชื่อเมนูตามรายการในฟอร์มแล้วส่งอีกครั้งค่ะ",
        )
        return True

    save_order_state(
        user_id,
        {
            "customer": profile_doc.customer,
            "orders": [{"item_code": o["item"].name, "title": o["title"], "qty": o["qty"]} for o in orders],
            "note": note,
        },
    )

    lines = ["สรุปออเดอร์", f"ลูกค้า: {profile_doc.customer}"]
    for o in orders:
        lines.append(f"- {o['title']} จำนวน: {o['qty']}")
    if note:
        lines.append(f"หมายเหตุ: {note}")
    lines.append('พิมพ์ "ยืนยัน" เพื่อสร้างออเดอร์ หรือ "ยกเลิก" หากต้องการแก้ไข')
    reply_message(reply_token, "\n".join(lines))
    return True


def finalize_order_from_state(profile_doc, state, reply_token, settings):
    """Create Sales Order from cached state after user confirms."""
    logger = frappe.logger("line_webhook")
    if not state or not state.get("orders"):
        reply_message(
            reply_token,
            "ไม่พบออเดอร์ที่รอยืนยัน กรุณาพิมพ์ \"สั่งออเดอร์\" เพื่อเริ่มใหม่ค่ะ",
        )
        return True
    if not settings.auto_create_sales_order or not settings.require_order_confirmation:
        reply_message(reply_token, "ระบบไม่ได้เปิดสร้าง Sales Order อัตโนมัติค่ะ")
        return True
    if not profile_doc.customer:
        reply_message(reply_token, "ยังไม่พบข้อมูลสมาชิก กรุณาลงทะเบียนก่อนนะคะ")
        return True

    orders = state.get("orders") or []
    note = state.get("note") or ""
    try:
        weekday = now_datetime().weekday()  # Monday=0, Saturday=5
        days_until_sat = (5 - weekday + 7) % 7  # 0..6

        so = frappe.get_doc(
            {
                "doctype": "Sales Order",
                "customer": profile_doc.customer,
                "transaction_date": today(),
                "delivery_date": add_days(today(), days_until_sat),
                "ignore_pricing_rule": 1,
                "remarks": note,
                "items": build_so_items(orders, settings),
            }
        )
        so.insert(ignore_permissions=True)
        so.submit()

        total_text = fmt_money(so.grand_total, currency=so.currency)
        reply_message(
            reply_token,
            f"รับออเดอร์แล้ว สร้าง Sales Order {so.name} จำนวน {len(orders)} รายการ ยอดรวม {total_text}",
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Order Auto-create Error")
        reply_message(
            reply_token,
            "ขออภัย ระบบยังไม่สามารถสร้าง Sales Order ได้ กรุณาลองใหม่หรือให้แอดมินช่วยดำเนินการค่ะ",
        )
    return True


def reply_registered_flex(profile_doc, reply_token, settings):
    customer = profile_doc.customer
    details = frappe.db.get_value(
        "Customer",
        customer,
        ["customer_name", "mobile_no"],
        as_dict=True,
    ) if customer else None

    display_name = (details and details.get("customer_name")) or customer or "สมาชิก"
    phone = (details and details.get("mobile_no")) or "-"
    points_button_text = first_keyword(
        collect_keywords(settings, "points", ["ตรวจสอบ Point คงเหลือ"])["raw"],
        default="ตรวจสอบ Point คงเหลือ",
    )
    order_button_text = first_keyword(
        collect_keywords(settings, "order", [settings.order_keyword or "สั่งออเดอร์"])["raw"],
        default="สั่งออเดอร์",
    )

    flex = {
        "type": "flex",
        "altText": "ข้อมูลสมาชิก",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "สมาชิก", "weight": "bold", "size": "lg"},
                    {"type": "text", "text": display_name, "size": "md", "margin": "sm"},
                    {"type": "text", "text": f"เบอร์: {phone}", "size": "sm", "color": "#555555", "margin": "sm"},
                ],
                "spacing": "md",
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#22bb33",
                        "action": {"type": "message", "label": "เช็คคะแนนคงเหลือ", "text": points_button_text},
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "action": {"type": "message", "label": "สั่งออเดอร์", "text": order_button_text},
                    },
                ],
            },
        },
    }

    reply_message(reply_token, flex)


def parse_keywords(raw_text, defaults=None):
    """Parse a comma/newline-separated string into a list of trimmed keywords."""
    defaults = defaults or []
    raw = (raw_text or "").strip() if isinstance(raw_text, str) else ""
    parts = []
    if raw:
        parts.extend([p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()])
    if not parts and defaults:
        parts = [p for p in defaults if p]
    return parts


def normalize_keywords(keywords):
    return set("".join(p.lower().split()) for p in keywords if p)


def collect_keywords(settings, kind, defaults=None):
    """Return both raw list and normalized set of keywords for a kind."""
    defaults = defaults or []
    kind_label = KEYWORD_KIND_MAP.get(kind, kind).lower()
    # Prefer simple text fields (allowing comma or newline separated keywords)
    legacy_map = {
        "register": settings.register_keywords,
        "points": settings.points_keywords,
        "menu": settings.menu_keywords,
        "order": settings.order_keyword,
    }
    legacy_raw = legacy_map.get(kind)
    raw_keywords = parse_keywords(legacy_raw, defaults)

    return {
        "raw": raw_keywords,
        "normalized": normalize_keywords(raw_keywords),
    }


def first_keyword(raw_text, default):
    if isinstance(raw_text, (list, tuple, set)):
        for item in raw_text:
            t = str(item or "").strip()
            if t:
                return t
    raw = (raw_text or "").replace("\n", ",")
    for part in raw.split(","):
        t = part.strip()
        if t:
            return t
    return default


def normalize_key(val):
    return "".join((val or "").lower().split())


def parse_orders_from_text(text, item_map):
    orders = []
    unknown = []
    note = ""
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.lower().startswith("สั่งออเดอร์"):
            continue
        if line.lower().startswith("หมายเหตุ"):
            note = line.split(":", 1)[1].strip() if ":" in line else line.replace("หมายเหตุ", "", 1).strip()
            continue
        match = QTY_PATTERN.match(line)
        if not match:
            continue
        name = match.group("name").strip()
        qty = float(match.group("qty") or 0)
        if qty <= 0:
            continue
        key = normalize_key(name)
        item = item_map.get(key)
        if not item:
            for k, candidate in item_map.items():
                if key in k or k in key:
                    item = candidate
                    break
        if item:
            orders.append({"item": item, "qty": qty, "line": line, "title": item.item_name or item.name})
        else:
            unknown.append(name)
    return orders, unknown, note


def resolve_public_image_url(path, logger=None):
    """Return absolute URL only if the image is public; otherwise return None."""
    if not path:
        return None
    # If already absolute URL, use it as-is
    if isinstance(path, str) and path.startswith(("http://", "https://")):
        return path
    try:
        file_doc = frappe.db.get_value(
            "File",
            {"file_url": path},
            ["file_url", "is_private"],
            as_dict=True,
        )
    except Exception:
        file_doc = None
    if file_doc and file_doc.get("is_private"):
        if logger:
            logger.warning({"event": "line_image_private", "file_url": path})
        return None
    try:
        return get_url((file_doc and file_doc.get("file_url")) or path)
    except Exception:
        if logger:
            logger.warning({"event": "line_image_get_url_failed", "file_url": path})
        return None


def fetch_menu_items(limit=10, order_by="item_name asc"):
    return frappe.get_all(
        "Item",
        filters={"custom_add_in_line_menu": 1},
        fields=["name", "item_name", "description", "custom_line_menu_image"],
        order_by=order_by,
        limit=limit,
    )


def build_summary_bubble(image_url, title, subtitle, body_contents=None, aspect_ratio="1:1"):
    contents = body_contents or []
    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": contents,
            "spacing": "md",
        },
    }
    if image_url:
        bubble["hero"] = {
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": aspect_ratio,
            "aspectMode": "cover",
            "action": {"type": "uri", "label": "ดูภาพ", "uri": image_url},
        }
    # Ensure title/subtitle at top
    if contents == body_contents:
        bubble["body"]["contents"] = [
            {"type": "text", "text": title, "weight": "bold", "size": "lg"},
            {"type": "text", "text": subtitle, "size": "sm", "color": "#555555", "wrap": True, "margin": "sm"},
            *contents,
        ]
    return bubble


def build_item_bubble(item, logger=None):
    title = item.item_name or item.name
    desc = (item.description or "").strip()
    if len(desc) > 120:
        desc = desc[:117] + "..."
    image_url = resolve_public_image_url(getattr(item, "custom_line_menu_image", None) or item.get("custom_line_menu_image"), logger)

    body_contents = [
        {"type": "text", "text": title, "weight": "bold", "size": "md", "wrap": True},
    ]
    if desc:
        body_contents.append(
            {"type": "text", "text": desc, "size": "sm", "color": "#555555", "wrap": True}
        )

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#22bb33",
                    "action": {
                        "type": "message",
                        "label": "สั่งออเดอร์",
                        "text": "สั่งออเดอร์",
                    },
                }
            ],
        },
    }
    if image_url:
        bubble["hero"] = {
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
            "action": {"type": "uri", "label": "ดูภาพ", "uri": image_url},
        }
    return bubble


def build_so_items(orders, settings):
    """Construct SO items with optional manual quantity discount."""
    items = []
    threshold = int(settings.qty_discount_threshold or 0)
    regular_price = float(settings.qty_price_regular or 0)
    discount_price = float(settings.qty_price_discount or 0)
    apply_discount = (
        bool(getattr(settings, "enable_qty_discount", False))
        and threshold > 0
        and discount_price > 0
    )
    for row in orders:
        qty = row.get("qty") or 0
        item_row = {"item_code": row.get("item_code"), "qty": qty}
        if apply_discount:
            if qty >= threshold and discount_price > 0:
                item_row["rate"] = discount_price
            elif regular_price > 0:
                item_row["rate"] = regular_price
        items.append(item_row)
    return items


def register_customer(profile_doc, full_name, phone_number, reply_token):
    settings = get_settings()
    already_registered_msg = (
        settings.already_registered_message or DEFAULT_ALREADY_REGISTERED_MSG
    )
    register_prompt = settings.register_prompt or DEFAULT_REGISTER_PROMPT
    if not full_name:
        reply_message(reply_token, register_prompt)
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
                already_registered_msg.format(name=existing_customer),
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

        contact_name = frappe.db.get_value("Contact", {"mobile_no": phone_number}, "name")
        if contact_name:
            contact_doc = frappe.get_doc("Contact", contact_name)
            # Ensure the contact is linked to this customer
            already_linked = any(
                (lnk.link_doctype == "Customer" and lnk.link_name == customer.name)
                for lnk in (contact_doc.links or [])
            )
            if not already_linked:
                contact_doc.append(
                    "links",
                    {
                        "link_doctype": "Customer",
                        "link_name": customer.name,
                    },
                )
                contact_doc.save(ignore_permissions=True)
        else:
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


def order_cache_key(user_id):
    return f"line_order_pending:{user_id}"


def get_order_state(user_id):
    return frappe.cache().get_value(order_cache_key(user_id)) or {}


def save_order_state(user_id, state):
    frappe.cache().set_value(order_cache_key(user_id), state, expires_in_sec=900)


def clear_order_state(user_id):
    frappe.cache().delete_value(order_cache_key(user_id))


@frappe.whitelist(allow_guest=True)
def ping():
    """Simple health check to confirm module is loaded."""
    return "pong"
