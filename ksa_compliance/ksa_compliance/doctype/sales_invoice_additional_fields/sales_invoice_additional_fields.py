# Copyright (c) 2024, Lavaloon and contributors
# For license information, please see license.txt
from __future__ import annotations

import hashlib
import json
import uuid
from typing import cast

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.selling.doctype.customer.customer import Customer
# noinspection PyProtectedMember
from frappe import _
from frappe.core.doctype.file.file import File
from frappe.model.document import Document
from result import is_err

from ksa_compliance import zatca_api as api
from ksa_compliance import zatca_cli as cli
from ksa_compliance.generate_xml import generate_xml_file, generate_einvoice_xml_fielname
from ksa_compliance.invoice import InvoiceMode, InvoiceType
from ksa_compliance.ksa_compliance.doctype.zatca_business_settings.zatca_business_settings import (
    ZATCABusinessSettings)
from ksa_compliance.output_models.e_invoice_output_model import Einvoice


class SalesInvoiceAdditionalFields(Document):
    # begin: auto-generated types
    # This code is auto-generated. Do not modify anything in this block.

    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from frappe.types import DF
        from ksa_compliance.ksa_compliance.doctype.additional_seller_ids.additional_seller_ids import \
            AdditionalSellerIDs

        allowance_indicator: DF.Check
        allowance_vat_category_code: DF.Data | None
        amended_from: DF.Link | None
        buyer_additional_number: DF.Data | None
        buyer_additional_street_name: DF.Data | None
        buyer_building_number: DF.Data | None
        buyer_city: DF.Data | None
        buyer_country_code: DF.Data | None
        buyer_district: DF.Data | None
        buyer_postal_code: DF.Data | None
        buyer_provincestate: DF.Data | None
        buyer_street_name: DF.Data | None
        buyer_vat_registration_number: DF.Data | None
        charge_indicator: DF.Check
        charge_vat_category_code: DF.Data | None
        code_for_allowance_reason: DF.Data | None
        crypto_graphic_stamp: DF.Text | None
        invoice_counter: DF.Int
        invoice_hash: DF.Data | None
        invoice_line_allowance_reason: DF.Data | None
        invoice_line_allowance_reason_code: DF.Data | None
        invoice_line_charge_amount: DF.Float
        invoice_line_charge_base_amount: DF.Float
        invoice_line_charge_base_amount_reason: DF.Data | None
        invoice_line_charge_base_amount_reason_code: DF.Data | None
        invoice_line_charge_indicator: DF.Data | None
        invoice_line_charge_percentage: DF.Percent
        invoice_type_code: DF.Data | None
        invoice_type_transaction: DF.Data | None
        other_buyer_ids: DF.Table[AdditionalSellerIDs]
        payment_means_type_code: DF.Data | None
        prepayment_id: DF.Data | None
        prepayment_issue_date: DF.Date | None
        prepayment_issue_time: DF.Data | None
        prepayment_type_code: DF.Data | None
        prepayment_uuid: DF.Data | None
        prepayment_vat_category_tax_amount: DF.Float
        prepayment_vat_category_taxable_amount: DF.Float
        previous_invoice_hash: DF.Data | None
        qr_code: DF.SmallText | None
        reason_for_allowance: DF.Data | None
        reason_for_charge: DF.Data | None
        reason_for_charge_code: DF.Data | None
        sales_invoice: DF.Link
        sum_of_allowances: DF.Float
        sum_of_charges: DF.Float
        supply_end_date: DF.Data | None
        tax_currency: DF.Data | None
        uuid: DF.Data | None
        vat_exemption_reason_code: DF.Data | None
        vat_exemption_reason_text: DF.SmallText | None

    # end: auto-generated types

    def get_invoice_type(self, settings: ZATCABusinessSettings) -> InvoiceType:
        invoice_type: InvoiceType
        if settings.invoice_mode == InvoiceMode.Standard:
            invoice_type = 'Standard'
        elif settings.invoice_mode == InvoiceMode.Simplified:
            invoice_type = 'Simplified'
        else:
            if self.buyer_vat_registration_number:
                invoice_type = 'Standard'
            else:
                invoice_type = 'Simplified'
        return invoice_type

    def before_insert(self):
        self.set_invoice_counter_value()
        self.set_pih()
        self.generate_uuid()
        self.set_tax_currency()  # Set as "SAR" as a default tax currency value
        self.set_calculated_invoice_values()
        self.set_buyer_details(sl_id=self.sales_invoice)
        self.set_invoice_type_code()

    def after_insert(self):
        settings = ZATCABusinessSettings.for_invoice(self.sales_invoice)
        if not settings:
            return

        self.prepare_for_zatca(settings)

    def on_submit(self):
        settings = ZATCABusinessSettings.for_invoice(self.sales_invoice)
        if not settings:
            return

        if settings.is_live_sync:
            self.send_to_zatca(settings)

    def prepare_for_zatca(self, settings: ZATCABusinessSettings):
        invoice_type = self.get_invoice_type(settings)
        einvoice = Einvoice(sales_invoice_additional_fields_doc=self, invoice_type=invoice_type)
        # TODO: Revisit this logging
        frappe.log_error("ZATCA Result LOG", message=json.dumps(einvoice.result, indent=2))
        frappe.log_error("ZATCA Error LOG", message=json.dumps(einvoice.error_dic, indent=2))

        invoice_xml = generate_xml_file(einvoice.result)
        result = cli.sign_invoice(settings.lava_zatca_path, invoice_xml, settings.cert_path,
                                  settings.private_key_path)
        self.invoice_hash = result.invoice_hash
        self.qr_code = result.qr_code
        self.save()

        xml_filename = generate_einvoice_xml_fielname(settings.vat_registration_number,
                                                      einvoice.result['invoice']['issue_date'],
                                                      einvoice.result['invoice']['issue_time'],
                                                      einvoice.result['invoice']['id'])
        file = cast(File, frappe.get_doc(
            {
                "doctype": "File",
                "file_name": xml_filename,
                "attached_to_doctype": "Sales Invoice Additional Fields",
                "attached_to_name": self.name,
                "content": result.signed_invoice_xml,
            }))
        file.is_private = True
        file.insert()

    def send_to_zatca(self, settings: ZATCABusinessSettings) -> None:
        invoice_type = self.get_invoice_type(settings)
        signed_xml = self.get_signed_xml()
        if not signed_xml:
            frappe.throw(_('Could not find signed XML attachment'), title=_('ZATCA Error'))

        self.send_xml_via_api(signed_xml, self.invoice_hash, invoice_type, settings)

    def generate_uuid(self):
        self.uuid = str(uuid.uuid4())

    def set_invoice_type_code(self):
        """
        A code of the invoice subtype and invoices transactions.
        The invoice transaction code must exist and respect the following structure:
        - [NNPNESB] where
        - NN (positions 1 and 2) = invoice subtype: - 01 for tax invoice - 02 for simplified tax invoice.
        - P (position 3) = 3rd Party invoice transaction, 0 for false, 1 for true
        - N (position 4) = Nominal invoice transaction, 0 for false, 1 for true
        - E (position 5) = Exports invoice transaction, 0 for false, 1 for true
        - S (position 6) = Summary invoice transaction, 0 for false, 1 for true
        - B (position 7) = Self billed invoice. Self-billing is not allowed (KSA-2, position 7 cannot be ""1"") for
        export invoices (KSA-2, position 5 = 1).
        """
        # Basic Simplified or Tax invoice
        self.invoice_type_transaction = "0100000" if self.buyer_vat_registration_number is None or "" else "0200000"

        is_debit, is_credit = frappe.db.get_value("Sales Invoice", self.sales_invoice,
                                                  ["is_debit_note", "is_return"])
        if is_debit:
            self.invoice_type_code = "383"
        elif is_credit:
            self.invoice_type_code = "381"
        else:
            self.invoice_type_code = "383"

    def set_tax_currency(self):
        self.tax_currency = "SAR"

    def set_invoice_counter_value(self):
        additional_field_records = frappe.db.get_list(self.doctype,
                                                      filters={"docstatus": ["!=", 2],
                                                               "invoice_counter": ["is", "set"]})
        if additional_field_records:
            self.invoice_counter = len(additional_field_records) + 1
        else:
            self.invoice_counter = 1

    def set_pih(self):
        if self.invoice_counter == 1:
            self.previous_invoice_hash = \
                "NWZlY2ViNjZmZmM4NmYzOGQ5NTI3ODZjNmQ2OTZjNzljMmRiYzIzOWRkNGU5MWI0NjcyOWQ3M2EyN2ZiNTdlOQ=="
        else:
            psi_id = frappe.db.get_value(self.doctype, filters={"invoice_counter": self.invoice_counter - 1},
                                         fieldname="sales_invoice")
            attachments = frappe.get_all("File", fields=("name", "file_name", "attached_to_name", "file_url"),
                                         filters={"attached_to_name": ("in", psi_id),
                                                  "attached_to_doctype": "Sales Invoice"})
            if attachments:  # Means that the invoice XML had been generated and saved
                site = frappe.local.site
                for attachment in attachments:
                    if attachment.file_name and attachment.file_name.endswith(".xml"):
                        xml_filename = attachment.file_name
                        break

                file_name = "private/files/" + xml_filename
                with open(file_name, "rb") as f:
                    data = f.read()
                    sha256hash = hashlib.sha256(data).hexdigest()
                self.previous_invoice_hash = sha256hash

    def set_buyer_details(self, sl_id: str):
        sl = cast(SalesInvoice, frappe.get_doc("Sales Invoice", sl_id))
        customer_doc = cast(Customer, frappe.get_doc("Customer", sl.customer))

        self.buyer_vat_registration_number = customer_doc.custom_vat_registration_number

        for item in customer_doc.get("custom_additional_ids"):
            self.append("other_buyer_ids",
                        {"type_name": item.type_name, "type_code": item.type_code, "value": item.value})

    def set_calculated_invoice_values(self):
        sinv = cast(SalesInvoice, frappe.get_doc("Sales Invoice", self.sales_invoice))
        self.set_sum_of_charges(sinv.taxes)
        self.set_sum_of_allowances(sales_invoice_doc=sinv)

    def send_xml_via_api(self, invoice_xml: str, invoice_hash: str, invoice_type: InvoiceType,
                         settings: ZATCABusinessSettings):
        if invoice_type == 'Standard':
            result = api.clear_invoice(server=settings.fatoora_server_url, invoice_xml=invoice_xml,
                                       invoice_uuid=self.uuid, invoice_hash=invoice_hash,
                                       security_token=settings.production_security_token,
                                       secret=settings.production_secret)
        else:
            result = api.report_invoice(server=settings.fatoora_server_url, invoice_xml=invoice_xml,
                                        invoice_uuid=self.uuid, invoice_hash=invoice_hash,
                                        security_token=settings.production_security_token,
                                        secret=settings.production_secret)

        # TODO: This is questionable. It's better to update the document itself to reflect the result. Not sure whether
        #   we also need to store the raw response
        status = ''
        if is_err(result):
            zatca_message = result.err_value
        else:
            zatca_message = json.dumps(result.ok_value)
            status = result.ok_value.status

        integration_dict = {
            "doctype": "ZATCA Integration Log",
            "invoice_reference": self.sales_invoice,
            "invoice_additional_fields_reference": self.name,
            "zatca_message": zatca_message,
            "zatca_status": status,
        }
        integration_doc = frappe.get_doc(integration_dict)
        integration_doc.insert()

    def set_sum_of_charges(self, taxes: list):
        total = 0
        if taxes:
            for item in taxes:
                total = total + item.tax_amount
        self.sum_of_charges = total

    def set_sum_of_allowances(self, sales_invoice_doc):
        self.sum_of_allowances = sales_invoice_doc.get("total") - sales_invoice_doc.get("net_total")

    def get_signed_xml(self) -> str | None:
        attachments = frappe.get_all("File", fields=("name", "file_name", "attached_to_name", "file_url"),
                                     filters={"attached_to_name": self.name,
                                              "attached_to_doctype": "Sales Invoice Additional Fields"})
        if not attachments:
            return None

        name: str | None = None
        for attachment in attachments:
            if attachment.file_name and attachment.file_name.endswith(".xml"):
                name = attachment.name
                break

        if not name:
            return None

        file = cast(File, frappe.get_doc("File", name))
        content = file.get_content()
        if isinstance(content, bytes):
            return content.decode('utf-8')
        return content


def customer_has_registration(customer_id: str):
    customer_doc = cast(Customer, frappe.get_doc("Customer", customer_id))
    if customer_doc.custom_vat_registration_number in (None, "") and all(
            ide.value in (None, "") for ide in customer_doc.custom_additional_ids):
        return False
    return True
