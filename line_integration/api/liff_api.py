"""
LIFF API Endpoints
==================
Guest-accessible REST endpoints called from the LINE LIFF frontend.
Authentication: LIFF access_token → LINE verify API → line_user_id lookup.
"""

import json
import re

import requests
import frappe
from frappe.utils import add_days, fmt_money, now_datetime, today

from line_integration.utils.line_client import get_settings, ensure_profile
from line_integration.api.line_webhook import (
    fetch_menu_items,
    resolve_public_image_url,
    format_qty,
    build_so_items,
    DEFAULT_LOYALTY_PROGRAM,
    PHONE_REGEX,
)


# ──────────────────────────────────────────────
#  Auth helpers
# ──────────────────────────────────────────────

def _verify_liff_token(access_token):
    """Verify LIFF access token with LINE and return user profile.

    Steps:
        1. Verify token → get client_id + expires_in
        2. Fetch profile using the same token → get userId, displayName, pictureUrl
    Returns dict with user info or raises.
    """
    if not access_token:
        frappe.throw("Missing access_token", frappe.AuthenticationError)

    # Step 1: Verify the token
    verify_resp = requests.get(
        "https://api.line.me/oauth2/v2.1/verify",
        params={"access_token": access_token},
        timeout=10,
    )
    if verify_resp.status_code != 200:
        frappe.throw("Invalid or expired LIFF access token", frappe.AuthenticationError)

    verify_data = verify_resp.json()
    if verify_data.get("expires_in", 0) <= 0:
        frappe.throw("LIFF access token expired", frappe.AuthenticationError)

    # Step 2: Fetch user profile
    profile_resp = requests.get(
        "https://api.line.me/v2/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if profile_resp.status_code != 200:
        frappe.throw("Failed to fetch LINE profile", frappe.AuthenticationError)

    profile = profile_resp.json()
    return {
        "user_id": profile.get("userId"),
        "display_name": profile.get("displayName"),
        "picture_url": profile.get("pictureUrl"),
        "status_message": profile.get("statusMessage"),
    }


def _get_liff_user(access_token):
    """Verify token, ensure LINE Profile doc exists, return (profile_doc, user_info)."""
    user_info = _verify_liff_token(access_token)
    user_id = user_info.get("user_id")
    if not user_id:
        frappe.throw("Could not determine LINE user ID", frappe.AuthenticationError)

    # ensure_profile creates or updates the LINE Profile doc
    profile_doc = ensure_profile(user_id)
    return profile_doc, user_info


# ──────────────────────────────────────────────
#  1. Auth endpoint
# ──────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def liff_auth(access_token=None):
    """Verify LIFF access token and return user profile + customer info.

    POST /api/method/line_integration.line_integration.api.liff_api.liff_auth
    Body: { "access_token": "<liff.getAccessToken()>" }

    Returns:
        {
            "user_id": "U...",
            "display_name": "...",
            "picture_url": "...",
            "is_registered": true/false,
            "customer_name": "..." or null,
            "phone": "..." or null
        }
    """
    profile_doc, user_info = _get_liff_user(access_token)

    customer_data = {}
    if profile_doc.customer:
        customer_data = frappe.db.get_value(
            "Customer",
            profile_doc.customer,
            ["customer_name", "mobile_no"],
            as_dict=True,
        ) or {}

    return {
        "user_id": user_info.get("user_id"),
        "display_name": user_info.get("display_name"),
        "picture_url": user_info.get("picture_url"),
        "is_registered": bool(profile_doc.customer),
        "customer_name": customer_data.get("customer_name"),
        "customer_id": profile_doc.customer,
        "phone": customer_data.get("mobile_no"),
    }


# ──────────────────────────────────────────────
#  2. Menu endpoint
# ──────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def liff_get_menu():
    """Return menu items for LIFF display.

    GET /api/method/line_integration.line_integration.api.liff_api.liff_get_menu

    Returns list of items:
        [{ "item_code": "...", "item_name": "...", "description": "...", "image_url": "..." }, ...]
    """
    items = fetch_menu_items(limit=50)
    result = []
    for item in items:
        image_url = resolve_public_image_url(
            item.get("custom_line_menu_image")
        )
        result.append({
            "item_code": item.name,
            "item_name": item.item_name or item.name,
            "description": (item.get("description") or "").strip(),
            "image_url": image_url,
        })
    return result


# ──────────────────────────────────────────────
#  3. Submit order endpoint
# ──────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def liff_submit_order(access_token=None, items=None, note=None):
    """Create a Sales Order from the LIFF frontend.

    POST /api/method/line_integration.line_integration.api.liff_api.liff_submit_order
    Body: {
        "access_token": "<token>",
        "items": [{"item_code": "...", "qty": 2}, ...],
        "note": "optional note"
    }
    """
    profile_doc, user_info = _get_liff_user(access_token)
    settings = get_settings()

    if not settings.auto_create_sales_order:
        frappe.throw("ระบบไม่ได้เปิดสร้าง Sales Order อัตโนมัติ")

    if not profile_doc.customer:
        frappe.throw("กรุณาสมัครสมาชิกก่อนสั่งออเดอร์", frappe.ValidationError)

    # Parse items
    if isinstance(items, str):
        items = json.loads(items)
    if not items or not isinstance(items, list):
        frappe.throw("กรุณาเลือกสินค้าอย่างน้อย 1 รายการ", frappe.ValidationError)

    # Validate items exist in menu
    menu_items = fetch_menu_items(limit=1000)
    valid_codes = {m.name for m in menu_items}

    orders = []
    for entry in items:
        item_code = entry.get("item_code")
        qty = float(entry.get("qty") or 0)
        if qty <= 0:
            continue
        if item_code not in valid_codes:
            frappe.throw(f"ไม่พบสินค้า: {item_code}", frappe.ValidationError)
        orders.append({
            "item_code": item_code,
            "qty": qty,
            "title": entry.get("item_name") or item_code,
        })

    if not orders:
        frappe.throw("ไม่มีรายการสินค้าที่ถูกต้อง", frappe.ValidationError)

    note = (note or "").strip()

    # Calculate delivery date (next Saturday)
    weekday = now_datetime().weekday()
    days_until_sat = (5 - weekday + 7) % 7
    if days_until_sat == 0:
        days_until_sat = 7  # if today is Saturday, deliver next Saturday

    try:
        so = frappe.get_doc({
            "doctype": "Sales Order",
            "customer": profile_doc.customer,
            "transaction_date": today(),
            "delivery_date": add_days(today(), days_until_sat),
            "ignore_pricing_rule": 1,
            "remarks": note,
            "line_order_note": note,
            "items": build_so_items(orders, settings),
        })
        so.insert(ignore_permissions=True)
        so.submit()

        total_text = fmt_money(so.grand_total, currency=so.currency)
        return {
            "success": True,
            "sales_order": so.name,
            "total_items": len(orders),
            "total_qty": sum(o["qty"] for o in orders),
            "grand_total": so.grand_total,
            "grand_total_formatted": total_text,
            "currency": so.currency,
        }
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LIFF Order Error")
        frappe.throw("ไม่สามารถสร้างออเดอร์ได้ กรุณาลองใหม่อีกครั้ง")


# ──────────────────────────────────────────────
#  4. Register / Link customer endpoint
# ──────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def liff_register(access_token=None, phone=None):
    """Register or link a customer by phone number.

    POST /api/method/line_integration.line_integration.api.liff_api.liff_register
    Body: { "access_token": "<token>", "phone": "0812345678" }
    """
    profile_doc, user_info = _get_liff_user(access_token)

    phone = (phone or "").strip()
    if not phone or not PHONE_REGEX.match(phone):
        frappe.throw("กรุณาใส่หมายเลขโทรศัพท์ 10 หลัก", frappe.ValidationError)

    if profile_doc.customer:
        # Already registered
        customer_data = frappe.db.get_value(
            "Customer",
            profile_doc.customer,
            ["customer_name", "mobile_no"],
            as_dict=True,
        ) or {}
        return {
            "status": "already_registered",
            "customer_name": customer_data.get("customer_name"),
            "phone": customer_data.get("mobile_no"),
        }

    # Check if phone is already linked to an existing customer
    existing_customer = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")

    if existing_customer:
        # Link existing customer to this LINE Profile
        profile_doc.customer = existing_customer
        profile_doc.status = "Active"
        profile_doc.last_seen = now_datetime()
        profile_doc.save(ignore_permissions=True)

        customer_data = frappe.db.get_value(
            "Customer",
            existing_customer,
            ["customer_name", "mobile_no"],
            as_dict=True,
        ) or {}
        return {
            "status": "linked",
            "customer_name": customer_data.get("customer_name"),
            "phone": customer_data.get("mobile_no"),
        }

    # Create new customer
    display_name = (
        user_info.get("display_name")
        or profile_doc.display_name
        or "LINE User"
    )

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

    customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": display_name,
        "customer_type": "Individual",
        "customer_group": customer_group,
        "territory": territory,
        "mobile_no": phone,
    }).insert(ignore_permissions=True)

    # Create contact
    contact_name = frappe.db.get_value("Contact", {"mobile_no": phone}, "name")
    if contact_name:
        contact_doc = frappe.get_doc("Contact", contact_name)
        already_linked = any(
            (lnk.link_doctype == "Customer" and lnk.link_name == customer.name)
            for lnk in (contact_doc.links or [])
        )
        if not already_linked:
            contact_doc.append("links", {
                "link_doctype": "Customer",
                "link_name": customer.name,
            })
            contact_doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "Contact",
            "first_name": display_name,
            "mobile_no": phone,
            "phone": phone,
            "links": [{"link_doctype": "Customer", "link_name": customer.name}],
        }).insert(ignore_permissions=True)

    # Link to LINE Profile
    profile_doc.customer = customer.name
    profile_doc.status = "Active"
    profile_doc.last_seen = now_datetime()
    profile_doc.save(ignore_permissions=True)

    return {
        "status": "registered",
        "customer_name": customer.customer_name or customer.name,
        "phone": phone,
    }


# ──────────────────────────────────────────────
#  5. Loyalty points endpoint
# ──────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def liff_get_points(access_token=None):
    """Return loyalty points balance for the authenticated LINE user.

    POST /api/method/line_integration.line_integration.api.liff_api.liff_get_points
    Body: { "access_token": "<token>" }
    """
    profile_doc, user_info = _get_liff_user(access_token)

    if not profile_doc.customer:
        return {
            "is_registered": False,
            "points": 0,
            "customer_name": None,
        }

    settings = get_settings()
    loyalty_program = settings.loyalty_program or DEFAULT_LOYALTY_PROGRAM
    points = 0

    try:
        from erpnext.accounts.doctype.loyalty_program.loyalty_program import (
            get_loyalty_program_details_with_points,
        )
        lp_details = get_loyalty_program_details_with_points(
            customer=profile_doc.customer,
            loyalty_program=loyalty_program,
        )
        points = (lp_details or {}).get("loyalty_points", 0) or 0
    except Exception:
        frappe.log_error(frappe.get_traceback(), "LIFF Points Error")

    customer_name = (
        frappe.db.get_value("Customer", profile_doc.customer, "customer_name")
        or profile_doc.customer
    )

    return {
        "is_registered": True,
        "points": points,
        "points_formatted": format_qty(points),
        "customer_name": customer_name,
        "loyalty_program": loyalty_program,
    }
