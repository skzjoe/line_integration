import frappe


def execute():
    # Reload DocType definition to ensure is_single = 1 from fixtures
    frappe.reload_doc("line_integration", "doctype", "line_settings", force=1)

    # Force database flag to single
    frappe.db.set_value(
        "DocType", "LINE Settings", "issingle", 1, update_modified=False
    )

    # Drop old table if it exists (for non-single DocType) to avoid schema mismatches
    if frappe.db.table_exists("tabLINE Settings"):
        frappe.db.sql("DROP TABLE `tabLINE Settings`")

    frappe.clear_cache(doctype="LINE Settings")
