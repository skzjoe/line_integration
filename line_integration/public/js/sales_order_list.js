frappe.listview_settings["Sales Order"] = {
	onload(listview) {
		listview.page.add_menu_item(__("Copy Pending Items"), () => {
			frappe.call({
				method: "line_integration.api.quick_pay.get_pending_order_items",
			}).then((r) => {
				if (r.message) {
					frappe.utils.copy_to_clipboard(r.message);
					frappe.show_alert({ message: __("Pending items copied"), indicator: "green" });
				} else {
					frappe.show_alert({ message: __("No pending items"), indicator: "orange" });
				}
			});
		});
	},
};
