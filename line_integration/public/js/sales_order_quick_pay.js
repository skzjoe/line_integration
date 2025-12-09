frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.status === "Closed") {
			return;
		}
		frm.add_custom_button(
			__("Quick Pay"),
			() => quickPay(frm),
			__("Line Integration")
		);
		frm.add_custom_button(
			__("Request Payment"),
			() => requestPayment(frm),
			__("Line Integration")
		);
	},
});

function quickPay(frm) {
	frappe.call({
		method: "line_integration.api.quick_pay.quick_pay_sales_order",
		freeze: true,
		freeze_message: __("Creating Invoice and Payment..."),
		args: { sales_order: frm.doc.name },
	}).then((r) => {
		if (r.message) {
			frappe.msgprint({
				title: __("Quick Pay"),
				indicator: "green",
				message: r.message,
			});
			frm.reload_doc();
		}
	});
}

function requestPayment(frm) {
	frappe.call({
		method: "line_integration.api.quick_pay.request_payment",
		freeze: true,
		freeze_message: __("Sending payment request..."),
		args: { sales_order: frm.doc.name },
	}).then((r) => {
		if (r.message) {
			frappe.msgprint({
				title: __("Request Payment"),
				indicator: "green",
				message: r.message,
			});
		}
	});
}
