from __future__ import annotations
import json
from typing import cast

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from frappe.model.document import Document
from frappe.utils import get_date_str, get_time

from ksa_compliance.invoice import InvoiceType
from ksa_compliance.ksa_compliance.doctype.sales_invoice_additional_fields import sales_invoice_additional_fields
from ksa_compliance.ksa_compliance.doctype.zatca_business_settings.zatca_business_settings import (
    ZATCABusinessSettings)


def append_tax_details_into_item_lines(invoice_id, item_lines):
    item_wise_tax_details = frappe.db.sql("""
                SELECT item_wise_tax_detail  
                FROM `tabSales Taxes and Charges` 
                WHERE parent = %(invoice_id)s
            """, {"invoice_id": invoice_id}, as_dict=1) or []

    if item_wise_tax_details:
        items_taxes = item_wise_tax_details[0]["item_wise_tax_detail"]
        if isinstance(items_taxes, str):
            items_taxes = json.loads(items_taxes)
        for item in item_lines:
            if item["item_code"] in items_taxes:
                item["tax_percent"] = items_taxes[item["item_code"]][0]
                item["tax_amount"] = items_taxes[item["item_code"]][1]
                item["total_amount"] = items_taxes[item["item_code"]][1] + item["amount"]

    return item_lines


class Einvoice:
    # TODO: if batch doc = none validate business settings else pass

    def __init__(self,
                 sales_invoice_additional_fields_doc: 'sales_invoice_additional_fields.SalesInvoiceAdditionalFields',
                 invoice_type: InvoiceType = "Simplified", batch_doc=None):

        self.additional_fields_doc = sales_invoice_additional_fields_doc
        self.batch_doc = batch_doc
        self.result = {}
        self.error_dic = {}

        self.sales_invoice_doc = cast(SalesInvoice, frappe.get_doc("Sales Invoice",
                                                                   sales_invoice_additional_fields_doc.sales_invoice))
        self.business_settings_doc = ZATCABusinessSettings.for_invoice(self.sales_invoice_doc.name)

        # Get Business Settings and Seller Fields
        self.get_business_settings_and_seller_details()

        # Get Buyer Fields
        self.get_buyer_details(invoice_type=invoice_type)

        # Get E-Invoice Fields
        self.get_e_invoice_details()  # TODO: Implement the rest of fields

        # TODO: Delivery (Supply start and end dates)
        # TODO: Allowance Charge (Discount)
        # FIXME: IF invoice is pre-paid
        self.get_text_value(field_name="payment_means_type_code",
                            source_doc=self.additional_fields_doc,
                            required=False,
                            xml_name="PaymentMeansCode",
                            rules=["BR-KSA-16", "BR-49", "BR-CL-16", "BT-81", "BG-16"],
                            parent="invoice")

        if self.sales_invoice_doc.get("is_debit_note") or self.sales_invoice_doc.get("is_return"):
            # TODO: handle conditional view and mandatory in sales invoice doctype
            self.get_text_value(field_name="custom_return_reason",
                                source_doc=self.sales_invoice_doc,
                                required=True,
                                xml_name="instruction_note",
                                min_length=1,
                                max_length=1000,
                                rules=["BR-KSA-17", "BR-KSA-F-06", "KSA-10"],
                                parent="invoice")

        # TODO: payment mode return Payment by credit?
        # if self.sales_invoice_doc.get("mode_of_payment") == "Credit":
        self.get_text_value(field_name="mode_of_payment",
                            source_doc=self.sales_invoice_doc,
                            required=False,
                            xml_name="PaymentNote",
                            min_length=0,
                            max_length=1000,
                            rules=["BR-KSA-F-06", "KSA-22", "BG-17"],
                            parent="invoice")

        self.get_text_value(field_name="payment_account_identifier",
                            source_doc=self.sales_invoice_doc,
                            required=False,
                            xml_name="ID",
                            min_length=0,
                            max_length=127,
                            rules=["BR-KSA-F-06", "BR-61", "BT-84", "BG-17"],
                            parent="invoice")
        # TODO: handle conditional allowance indicator and for its child

        # <----- start document level allowance ----->
        # fields from 49 to 58  document level allowance
        self.get_bool_value(field_name="allowance_indicator",
                            source_doc=self.additional_fields_doc,
                            required=False,
                            xml_name="multiplier_factor_numeric",
                            rules=["BR-KSA-F-02", "BG-20"],
                            parent="invoice")

        if self.additional_fields_doc.allowance_indicator:
            self.get_float_value(field_name="document_level_allowance_percentage",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="charge_indicator",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-DEC-01", "BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BT-94", "BG-20"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="document_level_allowance_percentage",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="charge_indicator",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-DEC-01", "BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BT-94", "BG-20"],
                                 parent="invoice")
        if self.additional_fields_doc.allowance_indicator:
            self.get_float_value(field_name="document_level_allowance_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="amount",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-31", "BR-DEC-01",
                                        "BT-92", "BT-92"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="document_level_allowance_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="amount",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-31", "BR-DEC-01",
                                        "BT-92", "BT-92"],
                                 parent="invoice")
        if self.additional_fields_doc.allowance_indicator:
            self.get_float_value(field_name="document_level_allowance_base_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="amount",
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BR-DEC-02", "BT-93", "BG-20"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="document_level_allowance_base_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="amount",
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BR-DEC-02", "BT-93", "BG-20"],
                                 parent="invoice")
        if self.additional_fields_doc.allowance_indicator:
            self.get_text_value(field_name="document_level_allowance_vat_category_code",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="ID",
                                rules=["BR-KSA-18", "BR-32", "BR-O-13", "BR-CL-18", "BT-95", "BG-20"],
                                parent="invoice")
        else:
            self.get_text_value(field_name="document_level_allowance_vat_category_code",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="ID",
                                rules=["BR-KSA-18", "BR-32", "BR-O-13", "BR-CL-18", "BT-95", "BG-20"],
                                parent="invoice")
        if self.additional_fields_doc.allowance_indicator:
            self.get_float_value(field_name="document_level_allowance_vat_rate",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="percent",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-12", "BR-KSA-DEC-02", "BR-S-06", "BR-Z-06",
                                        "BR-E-06", "BT-96", "BG-20"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="document_level_allowance_vat_rate",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="percent",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-12", "BR-KSA-DEC-02", "BR-S-06", "BR-Z-06",
                                        "BR-E-06", "BT-96", "BG-20"],
                                 parent="invoice")
        if self.additional_fields_doc.allowance_indicator:
            self.get_text_value(field_name="reason_for_allowance",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="allowance_charge_reason",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-F-06", "BT-97", "BG-20"],
                                parent="invoice")
        else:
            self.get_text_value(field_name="reason_for_allowance",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="allowance_charge_reason",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-F-06", "BT-97", "BG-20"],
                                parent="invoice")
        if self.additional_fields_doc.allowance_indicator:
            self.get_text_value(field_name="code_for_allowance_reason",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="allowance_charge_reason_code",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-F-06", "BT-98", "BG-20"],
                                parent="invoice")
        else:
            self.get_text_value(field_name="code_for_allowance_reason",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="allowance_charge_reason_code",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-F-06", "BT-98", "BG-20"],
                                parent="invoice")
        # <----- end document level allowance ----->

        # Fields from 62 : 71 document level charge
        # <----- start document level charge ----->

        self.get_bool_value(field_name="charge_indicator",
                            source_doc=self.additional_fields_doc,
                            required=False,
                            xml_name="charge_indicator",
                            rules=["BR-KSA-F-02", "BG-21"],
                            parent="invoice")

        if self.additional_fields_doc.charge_indicator:
            self.get_float_value(field_name="charge_percentage",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="MultiplierFactorNumeric",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BT-101", "BG-21"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="charge_percentage",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="MultiplierFactorNumeric",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BT-101", "BG-21"],
                                 parent="invoice")
        if self.additional_fields_doc.charge_indicator:
            self.get_float_value(field_name="charge_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="amount",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-36", "BR-DEC-05",
                                        "BT-99", "BG-21"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="charge_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="amount",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-36", "BR-DEC-05",
                                        "BT-99", "BG-21"],
                                 parent="invoice")
        if self.additional_fields_doc.charge_indicator:
            self.get_float_value(field_name="charge_base_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="base_amount",
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BR-DEC-06", "BT-100", "BG-21"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="charge_base_amount",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="base_amount",
                                 rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05",
                                        "BR-DEC-06", "BT-100", "BG-21"],
                                 parent="invoice")
        if self.additional_fields_doc.charge_indicator:
            self.get_text_value(field_name="charge_vat_category_code",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="ID",
                                rules=["BR-KSA-18", "BR-37", "BR-CL-18", "BT-102", "BG-21"],
                                parent="invoice")
        else:
            self.get_text_value(field_name="charge_vat_category_code",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="ID",
                                rules=["BR-KSA-18", "BR-37", "BR-CL-18", "BT-102", "BG-21"],
                                parent="invoice")
        if self.additional_fields_doc.charge_indicator:
            self.get_float_value(field_name="charge_vat_rate",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="Percent",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-13", "BR-KSA-DEC-02", "BR-S-07", "BR-Z-07",
                                        "BR-E-07", "BT-103", "BG-21"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="charge_vat_rate",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="Percent",
                                 min_value=0,
                                 max_value=100,
                                 rules=["BR-KSA-13", "BR-KSA-DEC-02", "BR-S-07", "BR-Z-07",
                                        "BR-E-07", "BT-103", "BG-21"],
                                 parent="invoice")
        if self.additional_fields_doc.charge_indicator:
            self.get_text_value(field_name="reason_for_charge",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="allowance_charge_reason",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-21", "BR-KSA-F-06", "BT-104", "BG-21"],
                                parent="invoice")
        else:
            self.get_text_value(field_name="reason_for_charge",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="allowance_charge_reason",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-21", "BR-KSA-F-06", "BT-104", "BG-21"],
                                parent="invoice")
        if self.additional_fields_doc.charge_indicator:
            self.get_text_value(field_name="reason_for_charge_code",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="allowance_charge_reason_code",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-19", "BR-KSA-F-06", "BT-105", "BG-21"],
                                parent="invoice")
        else:
            self.get_text_value(field_name="reason_for_charge_code",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="allowance_charge_reason_code",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-19", "BR-KSA-F-06", "BT-105", "BG-21"],
                                parent="invoice")

        # <----- end document level charge ----->
        if self.additional_fields_doc.allowance_indicator:
            self.get_float_value(field_name="sum_of_allowances",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="AllowanceTotalAmount",
                                 rules=["BR-KSA-F-04", "BR-CO-11", "BR-DEC-10", "BT-107", "BG-22"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="sum_of_allowances",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="AllowanceTotalAmount",
                                 rules=["BR-KSA-F-04", "BR-CO-11", "BR-DEC-10", "BT-107", "BG-22"],
                                 parent="invoice")
        if self.additional_fields_doc.charge_indicator:
            self.get_float_value(field_name="sum_of_charges",
                                 source_doc=self.additional_fields_doc,
                                 required=True,
                                 xml_name="ChargeTotalAmount",
                                 rules=["BR-KSA-F-04", "BR-CO-12", "BR-DEC-11", "BT-108", "BG-22"],
                                 parent="invoice")
        else:
            self.get_float_value(field_name="sum_of_charges",
                                 source_doc=self.additional_fields_doc,
                                 required=False,
                                 xml_name="ChargeTotalAmount",
                                 rules=["BR-KSA-F-04", "BR-CO-12", "BR-DEC-11", "BT-108", "BG-22"],
                                 parent="invoice")

        #     TODO: validate Document level charge indicator is Hardcoded, conditional fields from 63 to 72

        # TODO: Add Conditional Case

        # Invoice Line
        self.get_bool_value(field_name="invoice_line_allowance_indicator",
                            source_doc=self.additional_fields_doc,
                            required=False,
                            xml_name="ID",
                            rules=["BR-KSA-F-06", "BR-61", "BT-84", "BG-17"],
                            parent="invoice")

        self.get_float_value(field_name='invoice_line_allowance_percentage',
                             source_doc=self.additional_fields_doc,
                             required=False,
                             xml_name="multiplier_factor_numeric",
                             min_value=0,
                             max_value=100,
                             rules=["BR-KSA-EN16931-03", "BR-KSA-EN16931-04", "BR-KSA-EN16931-05", "BT-101", "BG-21"],
                             parent="invoice")

        # TODO: Add Conditional Case
        self.get_float_value(field_name='invoice_line_charge_amount',
                             source_doc=self.additional_fields_doc,
                             required=False,
                             xml_name="MultiplierFactorNumeric",
                             rules=["BR-KSA-F-04", "BR-KSA-EN16931-03", "BR-36", "BR-DEC-05", "BT-99", "BG-21"],
                             parent="invoice")

        #     TAX ID Scheme is Hardcoded

    # --------------------------- START helper functions ------------------------------

    def get_text_value(self, field_name: str, source_doc: Document, required: bool, xml_name: str = None,
                       min_length: int = 0, max_length: int = 5000, rules: list = None, parent: str = None):
        field_value = source_doc.get(field_name).strip() if source_doc.get(field_name) else None
        if required and field_value is None:
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return
        if field_value is None:
            return

        if not min_length <= len(field_value) <= max_length:
            self.error_dic[field_name] = f'Invalid {field_name} field size value {len(field_value)}'
            return

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = field_value
            else:
                self.result[parent] = {}
                self.result[parent][field_name] = field_value

        return field_value

    def get_bool_value(self, field_name: str, source_doc: Document, required: bool, xml_name: str = None,
                       rules: list = None, parent: str = None):
        field_value = source_doc.get(field_name) if source_doc.get(field_name) else None
        if required and field_value is None:
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return
        if field_value is None:
            return

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = field_value
            else:
                self.result[parent] = {}
                self.result[parent][field_name] = field_value

        return field_value

    def get_int_value(self, field_name: str, source_doc: Document, required: bool, min_value: int,
                      max_value: int, xml_name: str = None, rules: list = None, parent: str = None):
        field_value = cast(any, source_doc.get(field_name, None))
        if required and field_value is None:
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return

        if field_value is None:
            return
        field_value = int(field_value)
        if not min_value <= field_value <= max_value:
            self.error_dic[field_name] = f'field value must be between {min_value} and {max_value}'
            return

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = field_value
            else:
                self.result[parent] = {}
                self.result[parent][field_name] = field_value
        return field_value

    def get_float_value(self, field_name: str, source_doc: Document, required: bool, min_value: int = 0,
                        max_value: int = 999999999999, xml_name: str = None, rules: list = None, parent: str = None):
        field_value = cast(any, source_doc.get(field_name, None))
        if required and field_value is None:
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return
        if field_value is None:
            return

        # Try to parse
        field_value = float(field_value) if type(field_value) is int else field_value
        if not min_value <= field_value <= max_value:
            self.error_dic[field_name] = f'field value must be between {min_value} and {max_value}'
            return

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = field_value
            else:
                self.result[parent] = {}
                self.result[parent][field_name] = field_value
        return field_value

    def get_date_value(self, field_name, source_doc, required, xml_name, rules, parent):
        field_value = source_doc.get(field_name, None)
        if required and field_value is None:
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return
        if field_value is None:
            return

        # Try to parse
        field_value = get_date_str(field_value)

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = field_value
            else:
                self.result[parent] = {}
                self.result[parent][field_name] = field_value
        return field_value

    def get_float_child_value(self, field_name: str, field_value: float, required: bool, min_value: int = 0,
                              max_value: int = 999999999999, xml_name: str = None, rules: list = None,
                              parent: str = None):
        if field_value is None and required:
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return
        if field_value is None:
            return

        # Try to parse
        field_value = float(field_value) if type(field_value) is int else field_value
        if not min_value <= field_value <= max_value:
            self.error_dic[field_name] = f'field value must be between {min_value} and {max_value}'
            return

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = field_value
            else:
                self.result[parent] = {}
                self.result[parent][field_name] = field_value
        return field_value

    def get_time_value(self, field_name, source_doc, required, xml_name, rules, parent) -> str | None:
        field_value = source_doc.get(field_name, None)
        if required and not field_value:
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return
        if not field_value:
            return

        # We can't use frappe.utils.get_time_str because it results in invalid formats if any component is
        # single-digit, e.g. 00:04:05 is represented as 0:4:5. ZATCA tries to parse an ISO date/time format
        # created from the date and time joined with a T (e.g. 2024-02-20T00:04:05), and it fails to parse the
        # format produced by get_time_str
        formatted_value = get_time(field_value).strftime('%H:%M:%S')

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = formatted_value
            else:
                self.result[parent] = {}
                self.result[parent][field_name] = formatted_value
        return formatted_value

    def get_list_value(self, field_name: str, source_doc: Document, required: bool, xml_name: str = None,
                       rules: list = None, parent: str = None):
        field_value = source_doc.get(field_name)
        if required and (not field_value or {}):
            self.error_dic[field_name] = f"Missing field value: {field_name}."
            return
        if field_value is None or {}:
            return

        if xml_name == 'party_identifications':
            party_list = ["CRN", "MOM", "MLS", "700", "SAG", "OTH"]
            if field_value:
                field_value = self.validate_scheme_with_order(field_value=field_value, ordered_list=party_list)
                if not field_value:
                    self.error_dic[field_name] = f"Wrong ordered for field: {field_name}."
                    return

        field_name = xml_name if xml_name else field_name
        if parent:
            if self.result.get(parent):
                self.result[parent][field_name] = field_value
            else:
                self.result[parent] = {}
                if field_value:
                    self.result[parent][field_name] = field_value

        return field_value

    def has_any_other_buyer_id(self):
        buyer_id_list = self.additional_fields_doc.other_buyer_ids
        for item in buyer_id_list:
            if item.value not in (None, ""):
                return True
        return False

    # TODO: Complete the implementation
    def validate_scheme_with_order(self, field_value: dict, ordered_list: list):
        rem_ordered_list = ordered_list
        res = {}

        for value in field_value:
            type_code = value.get('type_code')
            value = value.get('value')
            if type_code not in ordered_list:
                self.error_dic['party_identification'] = f"Invalid scheme ID: {type_code} for Seller Additional IDs"
                return False
            elif type_code not in rem_ordered_list:
                self.error_dic['party_identification'] = (
                    f"Invalid scheme ID Order: "
                    f"for {field_value} in Additional IDs")
                return False
            elif value is not None:
                res[type_code] = value
                index = rem_ordered_list.index(type_code)
                rem_ordered_list = rem_ordered_list[index:]
        return res

    def get_customer_address_details(self, invoice_id):
        pass

    def get_customer_info(self, invoice_id):
        pass

    def get_business_settings_and_seller_details(self):
        # TODO: special validations handling
        self.get_list_value(field_name="other_ids",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="party_identifications",
                            rules=["BR-KSA-08", "BT-29", "BT-29-1", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="street",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="street_name",
                            min_length=1,
                            max_length=127,
                            rules=["BR-KSA-09", "BR-KSA-F-06", "BR-08", "BT-35", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="additional_street",
                            source_doc=self.business_settings_doc,
                            required=False,
                            xml_name="additional_street_name",
                            min_length=0,
                            max_length=127,
                            rules=["BR-KSA-F-06", "BR-08", "BT-36", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="building_number",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="building_number",
                            rules=["BR-KSA-09", "BR-KSA-37", "BR-08", "KSA-17", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="additional_address_number",  # TODO: Fix missing field
                            source_doc=self.business_settings_doc,
                            required=False,
                            xml_name="plot_identification",
                            rules=["BR-08", "KSA-23", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="city",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="city_name",
                            rules=["BR-KSA-09", "BR-KSA-F-06", "BR-08", "BT-37", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="postal_code",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="postal_zone",
                            rules=["BR-KSA-09", "BR-KSA-66", "BR-08", "BT-38", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="province_state",  # TODO: Fix missing field
                            source_doc=self.business_settings_doc,
                            required=False,
                            xml_name="CountrySubentity",
                            min_length=0,
                            max_length=127,
                            rules=["BR-KSA-F-06", "BR-08", "BT-39", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="district",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="city_subdivision_name",
                            min_length=1,
                            max_length=127,
                            rules=["BR-KSA-09", "BR-KSA-F-06", "BR-08", "KSA-3", "BG-5"],
                            parent="seller_details")

        self.get_text_value(field_name="country_code",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="country_code",
                            rules=["BG-5", "BT-40", "BR-08", "BR-09", "BR-CL-14"],
                            parent="seller_details")

        self.get_text_value(field_name="vat_registration_number",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="company_id",
                            rules=["BR-KSA-39", "BR-KSA-40", "BT-31", "BG-5"],
                            parent="business_settings")

        self.get_text_value(field_name="seller_name",
                            source_doc=self.business_settings_doc,
                            required=True,
                            xml_name="registration_name",
                            min_length=1,
                            max_length=1000,
                            rules=["BR-KSA-F-06", "BR-06", "BT-27", "BG-5"],
                            parent="business_settings")

        # --------------------------- END Business Settings and Seller Details fields ------------------------------

    def get_buyer_details(self, invoice_type):
        # --------------------------- START Buyer Details fields ------------------------------

        if invoice_type == "Standard":
            if self.additional_fields_doc.buyer_vat_registration_number is None or "":
                self.get_list_value(field_name="other_buyer_identification",
                                    # TODO: Fix Add to doctype customer and additional
                                    source_doc=self.sales_invoice_doc,
                                    required=True,
                                    xml_name="PartyIdentification",
                                    rules=["BR-KSA-08", "BT-29", "BT-29-1", "BG-5"],
                                    parent="buyer_details")
            else:
                self.get_list_value(field_name="other_buyer_identification",
                                    source_doc=self.sales_invoice_doc,
                                    required=False,
                                    xml_name="PartyIdentification",
                                    rules=["BR-KSA-08", "BT-29", "BT-29-1", "BG-5"],
                                    parent="buyer_details")
        if invoice_type == "Simplified":
            self.get_list_value(field_name="other_buyer_identification",
                                source_doc=self.sales_invoice_doc,
                                required=False,
                                xml_name="PartyIdentification",
                                rules=["BR-KSA-08", "BT-29", "BT-29-1", "BG-5"],
                                parent="buyer_details")
        if invoice_type == "Standard":
            self.get_text_value(field_name="buyer_street_name",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="street_name",
                                min_length=1,
                                max_length=127,
                                rules=["BR-KSA-10", "BR-KSA-63", "BR-KSA-F-06", "BR-10", "BT-50", "BG-8"],
                                parent="buyer_details")
        elif invoice_type == "Simplified":
            self.get_text_value(field_name="buyer_street_name",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="street_name",
                                min_length=0,
                                max_length=127,
                                rules=["BR-KSA-10", "BR-KSA-63", "BR-KSA-F-06", "BR-10", "BT-50", "BG-8"],
                                parent="buyer_details")

        self.get_text_value(field_name="buyer_additional_street_name",
                            source_doc=self.additional_fields_doc,
                            required=False,
                            xml_name="additional_street_name",
                            min_length=0,
                            max_length=127,
                            rules=["BR-KSA-F-06", "BT-51", "BG-8"],
                            parent="buyer_details")

        if invoice_type == "Standard":
            self.get_text_value(field_name="buyer_building_number",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="building_number",
                                rules=["KSA-18", "BG-8"],
                                parent="buyer_details")

        elif invoice_type == "Simplified":
            self.get_text_value(field_name="buyer_building_number",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="building_number",
                                rules=["KSA-18", "BG-8"],
                                parent="buyer_details")

        self.get_text_value(field_name="buyer_additional_number",
                            # TODO: add additional number field for address if needed
                            source_doc=self.additional_fields_doc,
                            required=False,
                            xml_name="plot_identification",
                            rules=["KSA-19", "BG-8"],
                            parent="buyer_details")

        if invoice_type == "Standard":
            self.get_text_value(field_name="buyer_city",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="city_name",
                                min_length=1,
                                max_length=127,
                                rules=["BR-KSA-10", "BR-KSA-63", "BR-KSA-F-06", "BR-10", "BT-52", "BG-8"],
                                parent="buyer_details")
        elif invoice_type == "Simplified":
            self.get_text_value(field_name="buyer_city",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="city_name",
                                min_length=0,
                                max_length=127,
                                rules=["BR-KSA-10", "BR-KSA-63", "BR-KSA-F-06", "BR-10", "BT-52", "BG-8"],
                                parent="buyer_details")

        if invoice_type == "Standard":
            self.get_text_value(field_name="postal_code",
                                source_doc=self.business_settings_doc,
                                required=True,
                                xml_name="postal_zone",
                                rules=["BR-KSA-09", "BR-KSA-66", "BR-08", "BT-38", "BG-5"],
                                parent="buyer_details")
        elif invoice_type == "Simplified":
            self.get_text_value(field_name="buyer_postal_code",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="postal_zone",
                                rules=["BR-10", "BT-53", "BG-8"],
                                parent="buyer_details")

        self.get_text_value(field_name="buyer_province_state",
                            source_doc=self.additional_fields_doc,
                            required=False,
                            xml_name="CountrySubentity",
                            min_length=0,
                            max_length=127,
                            rules=["BR-KSA-F-06", "BT-54", "BG-8"],
                            parent="buyer_details")

        if invoice_type == "Standard":
            self.get_text_value(field_name="buyer_district",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="city_subdivision_name",
                                min_length=0,
                                max_length=127,
                                rules=["BR-KSA-63", "BR-KSA-F-06", "KSA-4", "BG-8"],
                                parent="buyer_details")

        elif invoice_type == "Simplified":
            self.get_text_value(field_name="buyer_district",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="city_subdivision_name",
                                min_length=0,
                                max_length=127,
                                rules=["BR-KSA-63", "BR-KSA-F-06", "KSA-4", "BG-8"],
                                parent="buyer_details")

        if invoice_type == "Standard":
            self.get_text_value(field_name="buyer_country_code",
                                source_doc=self.additional_fields_doc,
                                required=True,
                                xml_name="identification_code",
                                rules=["BR-KSA-10", "BR-KSA-63", "BR-CL-14", "BR-10", "BT-55", "BG-8"],
                                parent="buyer_details")

        elif invoice_type == "Simplified":
            self.get_text_value(field_name="buyer_country_code",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="identification_code",
                                rules=["BR-KSA-10", "BR-KSA-63", "BR-CL-14", "BR-10", "BT-55", "BG-8"],
                                parent="buyer_details")

        if invoice_type == "Standard":
            if self.has_any_other_buyer_id():
                self.get_text_value(field_name="buyer_vat_registration_number",
                                    source_doc=self.additional_fields_doc,
                                    required=False,
                                    xml_name="company_id",
                                    rules=["BR-KSA-44", "BR-KSA-46", "BT-48", "BG-7"],
                                    parent="buyer_details")
            else:
                self.get_text_value(field_name="buyer_vat_registration_number",
                                    source_doc=self.additional_fields_doc,
                                    required=True,
                                    xml_name="company_id",
                                    rules=["BR-KSA-44", "BR-KSA-46", "BT-48", "BG-7"],
                                    parent="buyer_details")

        elif invoice_type == "Simplified":
            self.get_text_value(field_name="buyer_vat_registration_number",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="company_id",
                                rules=["BR-KSA-44", "BR-KSA-46", "BT-48", "BG-7"],
                                parent="buyer_details")

        if invoice_type == "Standard":
            self.get_text_value(field_name="customer_name",
                                source_doc=self.sales_invoice_doc,
                                required=True,
                                xml_name="registration_name",
                                min_length=1,
                                max_length=1000,
                                rules=["BR-KSA-25", "BR-KSA-42", "BR-KSA-71", "BR-KSA-F-06", "BT-44", "BG-7"],
                                parent="buyer_details")

        elif invoice_type == "Simplified" and self.sales_invoice_doc.get("customer_name"):
            self.get_text_value(field_name="customer_name",
                                source_doc=self.sales_invoice_doc,
                                required=False,
                                xml_name="registration_name",
                                min_length=0,
                                max_length=1000,
                                rules=["BR-KSA-25", "BR-KSA-42", "BR-KSA-71", "BR-KSA-F-06", "BT-44", "BG-7"],
                                parent="buyer_details")

        # --------------------------- END Buyer Details fields ------------------------------

    def get_e_invoice_details(self):
        # --------------------------- START Invoice fields ------------------------------
        # --------------------------- START Invoice Basic info ------------------------------
        self.get_text_value(field_name="name",
                            source_doc=self.sales_invoice_doc,
                            required=True,
                            xml_name="id",
                            rules=["BT-1", "BR-02", "BR-KSA-F-06"],
                            parent="invoice")

        self.get_text_value(field_name="uuid",
                            source_doc=self.additional_fields_doc,
                            required=True,
                            xml_name="uuid",
                            rules=["KSA-1", "BR-KSA-03"],
                            parent="invoice")

        self.get_date_value(field_name="posting_date",
                            source_doc=self.sales_invoice_doc,
                            required=True,
                            xml_name="issue_date",
                            rules=["BT-2", "BR-03", "BR-KSA-04", "BR-KSA-F-01"],
                            parent="invoice")

        self.get_time_value(field_name="posting_time",
                            source_doc=self.sales_invoice_doc,
                            required=True,
                            xml_name="issue_time",
                            rules=["KSA-25", "BR-KSA-70"],
                            parent="invoice")

        self.get_text_value(field_name="invoice_type_code",
                            source_doc=self.additional_fields_doc,
                            required=True,
                            xml_name="invoice_type_code",
                            rules=["BT-3", "BR-04", "BR-CL-01", "BR-KSA-05"],
                            parent="invoice")

        self.get_text_value(field_name="invoice_type_transaction",
                            source_doc=self.additional_fields_doc,
                            required=True,
                            xml_name="invoice_type_transaction",
                            rules=["KSA-2", "BR-KSA-06", "BR-KSA-07", "BR-KSA-31"],
                            parent="invoice")

        self.get_text_value(field_name="currency",
                            source_doc=self.sales_invoice_doc,
                            required=True,
                            xml_name="currency_code",
                            rules=["BT-5", "BR-05", "BR-CL-04", "BR-KSA-CL-02"],
                            parent="invoice")
        # Default "SAR"
        self.get_text_value(field_name="tax_currency",
                            source_doc=self.additional_fields_doc,
                            required=True,
                            xml_name="tax_currency",
                            rules=["BT-6", "BR-CL-05", "BR-KSA-EN16931-02", "BR-KSA-68"],
                            parent="invoice")

        self.get_int_value(field_name="invoice_counter",
                           source_doc=self.additional_fields_doc,
                           required=True,
                           xml_name="invoice_counter_value",
                           min_value=0,
                           max_value=999999999999,
                           rules=["KSA-16", "BR-KSA-33", "BR-KSA-34"],
                           parent="invoice")

        self.get_text_value(field_name="previous_invoice_hash",
                            source_doc=self.additional_fields_doc,
                            required=True,
                            xml_name="pih",
                            rules=["KSA-13", "BR-KSA-26", "BR-KSA-61"],
                            parent="invoice")

        self.get_text_value(field_name="qr_code",
                            source_doc=self.additional_fields_doc,
                            required=True,
                            xml_name="qr_code",
                            rules=["KSA-14", "BR-KSA-27"],
                            parent="invoice")
        self.get_text_value(field_name="crypto_graphic_stamp",
                            source_doc=self.additional_fields_doc,
                            required=True,
                            xml_name="crypto_graphic_stamp",
                            rules=[" KSA-15", "Digital Identity Standards", "BR-KSA-28", "BR-KSA-29", "BR-KSA-30",
                                   "BR-KSA-60"],
                            parent="invoice")

        # TODO: Purchasing Order Exists
        # TODO: Billing Reference if the invoice is credit or debit (return against field)
        # FIXME: Contracting (contract ID)
        if self.sales_invoice_doc.get("contract_id"):
            self.get_text_value(field_name="contract_id",
                                source_doc=self.additional_fields_doc,
                                required=False,
                                xml_name="contract_id",
                                rules=["BT-12", "BR-KSA-F-06"],
                                parent="invoice")

        self.get_float_value(field_name="total",
                             source_doc=self.sales_invoice_doc,
                             required=True,
                             xml_name="total",
                             rules=["BG-22", "BT-106", "BR-CO-10", "BR-DEC-09", "2"],
                             parent="invoice")

        self.get_float_value(field_name="net_total",
                             source_doc=self.sales_invoice_doc,
                             required=True,
                             xml_name="net_total",
                             rules=["BG-22", "BT-109", "BR-13", "BR-CO-13", "BR-DEC-12", "BR-KSA-F-04"],
                             parent="invoice")

        self.get_float_value(field_name="total_taxes_and_charges",
                             source_doc=self.sales_invoice_doc,
                             required=True,
                             xml_name="total_taxes_and_charges",
                             rules=["BG-22", "BT-110", "BR-CO-14", "BR-DEC-13", "BR-KSA-EN16931-08",
                                    "BR-KSA-EN16931-09",
                                    "BR-KSA-F-04"],
                             parent="invoice")
        # TODO: Tax Account Currency
        self.get_float_value(field_name="grand_total",
                             source_doc=self.sales_invoice_doc,
                             required=True,
                             xml_name="grand_total",
                             rules=["BG-22", "BT-112", "BR-14", "BR-CO-15", "BR-DEC-14", "BR-KSA-F-04"],
                             parent="invoice")
        self.get_float_value(field_name="total_advance",
                             source_doc=self.sales_invoice_doc,
                             required=False,
                             xml_name="prepaid_amount",
                             rules=["BR-KSA-73", "BR-DEC-16", "BT-113", "BG-22"],
                             parent="invoice")
        self.get_float_value(field_name="rounding_adjustment",
                             source_doc=self.sales_invoice_doc,
                             required=False,
                             xml_name="rounding_adjustment",
                             rules=["BG-22", "BT-114", "BR-DEC-17"],
                             parent="invoice")

        self.get_float_value(field_name="outstanding_amount",
                             source_doc=self.sales_invoice_doc,
                             required=False,
                             xml_name="outstanding_amount",
                             rules=["BG-22", "BT-115", "BR-15", "BR-CO-16", "BR-DEC-18"],
                             parent="invoice")
        self.get_float_value(field_name="net_amount",
                             source_doc=self.sales_invoice_doc,
                             required=True,
                             xml_name="VAT_category_taxable_amount",
                             rules=["BR-KSA-F-04", "BR-45", "BR-DEC-19", "BR-S-08", "BR-E-08", "BR-Z-08", "BR-O-08",
                                    "BR-CO-18", "BT-116", "BG-23"],
                             parent="invoice")
        try:
            self.get_float_child_value(field_name="taxes_rate",
                                       field_value=self.sales_invoice_doc.taxes[0].rate,
                                       required=False,
                                       xml_name="Percent",
                                       rules=["BR-KSA-DEC-02", "BR-48", "BR-CO-18", "BT-119", "BG-23"],
                                       parent="invoice")
        except Exception:
            self.error_dic["taxable_amount"] = f"Could not map to sales invoice taxes rate."

            # VAT exemption reason to be added in customizations

            # --------------------------- END Invoice Basic info ------------------------------
        # --------------------------- Start Getting Invoice's item lines ------------------------------
        # TODO : Add Rules for fields
        item_lines = []
        for item in self.sales_invoice_doc.get("items"):
            new_item = {}
            has_discount = False
            req_fields = ["idx", "qty", "uom", "item_code", "item_name", "net_amount", "amount", "price_list_rate",
                          "rate", "discount_percentage", "discount_amount"]
            if isinstance(item.discount_amount, float) and item.discount_amount > 0:
                has_discount = True
            for it in req_fields:
                if it not in ["item_name", "uom", "item_code"]:
                    if it in ["discount_percentage", "discount_amount"] and not has_discount:
                        new_item[it] = self.get_float_value(
                            field_name=it,
                            source_doc=item,
                            required=False,
                            min_value=-999999999,
                            max_value=999999999,
                            xml_name=it
                        )
                    else:
                        new_item[it] = self.get_float_value(
                            field_name=it,
                            source_doc=item,
                            required=True,
                            min_value=-999999999,
                            max_value=999999999,
                            xml_name=it
                        )
                elif it in ["item_name", "uom", "item_code"]:
                    new_item[it] = self.get_text_value(
                        field_name=it,
                        source_doc=item,
                        required=True,
                        xml_name=it
                    )
            item_lines.append(new_item)

        # Add tax amount and tax percent on each item line
        item_lines = append_tax_details_into_item_lines(self.sales_invoice_doc.name, item_lines)
        # Add invoice total taxes and charges percentage field
        self.result["invoice"]["total_taxes_and_charges_percent"] = sum(
            it.rate for it in self.sales_invoice_doc.get("taxes", []))
        self.result["invoice"]["item_lines"] = item_lines
        # --------------------------- END Getting Invoice's item lines ------------------------------
