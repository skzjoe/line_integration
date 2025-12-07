import frappe
from frappe.model.document import Document


class LINEProfile(Document):
    def validate(self):
        if not self.line_user_id:
            frappe.throw("LINE User ID is required.")
