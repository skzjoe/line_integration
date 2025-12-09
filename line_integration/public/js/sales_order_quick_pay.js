frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || frm.doc.status === "Closed") {
			return;
		}
		// Standalone quick copy button
		frm.add_custom_button(__("Copy Order Text"), () => copyOrderText(frm));

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
		frm.add_custom_button(
			__("Notify Customer (LINE)"),
			() => notifyCustomer(frm),
			__("Line Integration")
		);
		frm.add_custom_button(
			__("Print Bag Label"),
			() => printBagLabel(frm),
			__("Line Integration")
		);
	},
});

function quickPay(frm) {
	// ask if user wants to redeem loyalty points (if available)
	frappe.call({
		method: "line_integration.api.quick_pay.get_loyalty_balance",
		args: { sales_order: frm.doc.name },
	}).then((res) => {
		const data = res.message || {};
		const points = data.points || 0;
		const value_per_point = data.value_per_point || 0;
		const max_amount = data.max_amount || 0;
		const saved_points = data.saved_points || 0;

		if (points > 0 && value_per_point > 0 && max_amount > 0) {
			const d = new frappe.ui.Dialog({
				title: __("Redeem Loyalty Points"),
				fields: [
					{ fieldtype: "Read Only", label: __("Available Points"), default: points },
					{ fieldtype: "Read Only", label: __("Value per Point"), default: value_per_point },
					{ fieldtype: "Read Only", label: __("Max Amount"), default: max_amount },
					{
						fieldtype: "Int",
						label: __("Points to Redeem"),
						fieldname: "points_to_redeem",
						default: saved_points || 0,
						description: __("ใส่ 0 หากไม่ใช้คะแนน"),
					},
				],
				primary_action_label: __("Confirm"),
				primary_action: (values) => {
					d.hide();
					submitQuickPay(frm, values.points_to_redeem || 0);
				},
			});
			d.show();
		} else {
			submitQuickPay(frm, 0);
		}
	});
}

function submitQuickPay(frm, points_to_redeem) {
	frappe.call({
		method: "line_integration.api.quick_pay.quick_pay_sales_order",
		freeze: true,
		freeze_message: __("Creating Invoice and Payment..."),
		args: { sales_order: frm.doc.name, points_to_redeem },
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
		method: "line_integration.api.quick_pay.get_loyalty_balance",
		args: { sales_order: frm.doc.name },
	}).then((res) => {
		const data = res.message || {};
		const points = data.points || 0;
		const value_per_point = data.value_per_point || 0;
		const max_amount = data.max_amount || 0;

		if (points > 0 && value_per_point > 0 && max_amount > 0) {
			const d = new frappe.ui.Dialog({
				title: __("Redeem Loyalty Points"),
				fields: [
					{ fieldtype: "Read Only", label: __("Available Points"), default: points },
					{ fieldtype: "Read Only", label: __("Value per Point"), default: value_per_point },
					{ fieldtype: "Read Only", label: __("Max Amount"), default: max_amount },
					{
						fieldtype: "Int",
						label: __("Points to Redeem"),
						fieldname: "points_to_redeem",
						default: 0,
						description: __("ใส่ 0 หากไม่ใช้คะแนน"),
					},
				],
				primary_action_label: __("Confirm"),
				primary_action: (values) => {
					d.hide();
					submitRequestPayment(frm, values.points_to_redeem || 0);
				},
			});
			d.show();
		} else {
			submitRequestPayment(frm, 0);
		}
	});
}

function printBagLabel(frm) {
	const w = window.open(frappe.urllib.get_full_url(`/api/method/line_integration.api.quick_pay.print_bag_label?sales_order=${encodeURIComponent(frm.doc.name)}`));
	if (!w) {
		frappe.msgprint({ indicator: "orange", message: __("Please allow popups to print.") });
	}
}

function copyOrderText(frm) {
	frappe.call({
		method: "line_integration.api.quick_pay.get_order_copy_text",
		args: { sales_order: frm.doc.name },
	}).then((r) => {
		if (r.message) {
			frappe.utils.copy_to_clipboard(r.message);
			frappe.show_alert({ message: __("Order text copied"), indicator: "green" });
		}
	});
}

function submitRequestPayment(frm, points_to_redeem) {
	frappe.call({
		method: "line_integration.api.quick_pay.request_payment",
		freeze: true,
		freeze_message: __("Sending payment request..."),
		args: { sales_order: frm.doc.name, points_to_redeem },
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

function notifyCustomer(frm) {
	frappe.call({
		method: "line_integration.api.quick_pay.notify_sales_order",
		args: { sales_order: frm.doc.name },
	}).then((r) => {
		if (r.message) {
			frappe.msgprint({
				title: __("Notify Customer"),
				indicator: "green",
				message: r.message,
			});
		}
	});
}
