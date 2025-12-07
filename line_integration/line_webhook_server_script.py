"""LINE webhook handler for Server Script (no imports).
Endpoint (Server Script, API): /api/method/run_server_script?script_name=line_webhook
Allow Guest: Yes
Allow Imports: Off
"""

# Prompts
register_prompt = "Please tell us your name to get started."
ask_phone_prompt = "Thanks! Now please send your 10-digit phone number."


def reply_line(token, text):
    settings = frappe.get_single("LINE Settings")
    # defensive: skip if token missing or app not enabled/token empty
    if not token or not getattr(settings, "enabled", 0):
        return
    access_token = getattr(settings, "channel_access_token", "") or ""
    if not access_token:
        return
    try:
        payload = frappe.as_json(
            {"replyToken": token, "messages": [{"type": "text", "text": text}]}
        )
        frappe.make_post_request(
            "https://api.line.me/v2/bot/message/reply",
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + access_token,
            },
            data=payload,
            timeout=10,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Reply Error")


def cache_key(user_id):
    return "line_registration_state:" + user_id


def get_state(user_id):
    cached = frappe.cache().get_value(cache_key(user_id))
    return cached or {}


def save_state(user_id, state):
    frappe.cache().set_value(cache_key(user_id), state, expires_in_sec=3600)


def clear_state(user_id):
    frappe.cache().delete_value(cache_key(user_id))


def ensure_profile(user_id, event=None):
    profile_name = frappe.db.get_value("LINE Profile", {"line_user_id": user_id})
    doc = frappe.get_doc("LINE Profile", profile_name) if profile_name else frappe.new_doc("LINE Profile")
    if not profile_name:
        doc.line_user_id = user_id
        doc.status = "Active"

    source = (event or {}).get("source") or {}
    doc.display_name = source.get("displayName") or doc.display_name
    doc.picture_url = source.get("pictureUrl") or doc.picture_url
    doc.last_seen = frappe.utils.now_datetime()
    if event:
        doc.last_event = frappe.as_json(event)
    doc.save(ignore_permissions=True)
    return doc


def link_customer(profile_doc, phone_number, reply_token):
    customer_name = frappe.db.get_value("Customer", {"mobile_no": phone_number}, "name")
    if customer_name:
        profile_doc.customer = customer_name
        profile_doc.status = "Active"
        profile_doc.last_seen = frappe.utils.now_datetime()
        profile_doc.save(ignore_permissions=True)
        reply_line(reply_token, "Linked to customer " + customer_name + ". Thank you!")
    else:
        reply_line(reply_token, "Customer not found. Please contact support.")


def register_customer(profile_doc, full_name, phone_number, reply_token):
    if not full_name:
        reply_line(reply_token, "Please tell us your name to continue.")
        return
    if not phone_number or (len(phone_number) != 10) or (not phone_number.isdigit()):
        reply_line(reply_token, "Invalid phone number. Please send 10 digits.")
        return

    existing_customer = frappe.db.get_value(
        "Customer", {"mobile_no": phone_number}, "name"
    )

    try:
        if existing_customer:
            profile_doc.customer = existing_customer
            profile_doc.status = "Active"
            profile_doc.last_seen = frappe.utils.now_datetime()
            profile_doc.save(ignore_permissions=True)
            reply_line(
                reply_token,
                "Linked to existing customer " + existing_customer + ". Thank you!",
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
        profile_doc.last_seen = frappe.utils.now_datetime()
        profile_doc.save(ignore_permissions=True)

        reply_line(
            reply_token,
            "Created customer " + customer.name + " and linked to your LINE. Thank you!",
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Registration Error")
        reply_line(
            reply_token,
            "Sorry, we could not complete your registration right now. Please try again later.",
        )


# Main execution (no functions required by safeexec)
raw_body = frappe.request.get_data() or b""
signature = frappe.get_request_header("X-Line-Signature")

# Signature validation skipped (imports disabled); proceed but log if missing
if not signature:
    try:
        frappe.log_error("LINE Webhook Warning", "Missing signature header; skipping validation.")
    except Exception:
        pass

payload_text = ""
try:
    payload_text = raw_body.decode("utf-8")
except Exception:
    payload_text = ""

payload = frappe.parse_json(payload_text or "{}")
events = payload.get("events") or []

for event in events:
    try:
        event_type = event.get("type")
        source = event.get("source") or {}
        user_id = source.get("userId")
        if not user_id:
            continue

        profile_doc = ensure_profile(user_id, event)
        state = get_state(user_id)

        if event_type == "unfollow":
            profile_doc.status = "Blocked"
            profile_doc.last_event = frappe.as_json(event)
            profile_doc.save(ignore_permissions=True)
            continue

        if event_type == "follow":
            profile_doc.status = "Active"
            profile_doc.last_event = frappe.as_json(event)
            profile_doc.save(ignore_permissions=True)
            reply_line(event.get("replyToken"), "Thanks for following us!")
            continue

        if event_type == "message":
            message = event.get("message") or {}
            if message.get("type") == "text":
                text = (message.get("text") or "").strip()
                lower = text.lower()

                if state.get("stage") == "awaiting_name":
                    save_state(user_id, {"stage": "awaiting_phone", "name": text})
                    reply_line(event.get("replyToken"), ask_phone_prompt)
                    continue

                if state.get("stage") == "awaiting_phone":
                    if (len(text) == 10) and text.isdigit():
                        register_customer(
                            profile_doc, state.get("name", "").strip(), text, event.get("replyToken")
                        )
                        clear_state(user_id)
                    else:
                        reply_line(
                            event.get("replyToken"),
                            "Please send a valid 10-digit phone number.",
                        )
                    continue

                if lower == "register":
                    clear_state(user_id)
                    save_state(user_id, {"stage": "awaiting_name"})
                    reply_line(event.get("replyToken"), register_prompt)
                    continue

                if (len(text) == 10) and text.isdigit():
                    link_customer(profile_doc, text, event.get("replyToken"))
                    continue

            profile_doc.last_event = frappe.as_json(event)
            profile_doc.last_seen = frappe.utils.now_datetime()
            profile_doc.save(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LINE Webhook Error")

frappe.response["message"] = "OK"
