# © 2016-2017 Akretion (Alexis de Lattre <alexis.delattre@akretion.com>)
# Copyright 2020 Onestein (<https://www.onestein.eu>)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import logging
from io import BytesIO
from pathlib import Path

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero, float_round

logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "base.ubl"]

    ubl_export_done = fields.Boolean(default=False)

    @api.model
    def get_rfq_states(self):
        return ["draft", "sent", "to approve"]

    def _ubl_add_header(self, doc_type, parent_node, ns, version="2.1"):
        if doc_type == "rfq":
            now_utc = fields.Datetime.to_string(fields.Datetime.now())
            date = now_utc[:10]
            time = now_utc[11:]
            currency_node_name = "PricingCurrencyCode"
        elif doc_type == "order":
            date = self.date_order
            date = fields.Date.to_string(date)
            currency_node_name = "DocumentCurrencyCode"
        ubl_version = etree.SubElement(parent_node, ns["cbc"] + "UBLVersionID")
        ubl_version.text = version
        doc_id = etree.SubElement(parent_node, ns["cbc"] + "ID")
        doc_id.text = self.name
        issue_date = etree.SubElement(parent_node, ns["cbc"] + "IssueDate")
        issue_date.text = date
        if doc_type == "rfq":  # IssueTime is required on RFQ, not on order
            issue_time = etree.SubElement(parent_node, ns["cbc"] + "IssueTime")
            issue_time.text = time
        # if self.note:
        if self.partner_shipping_id:
            note = etree.SubElement(parent_node, ns["cbc"] + "Note")
            note.text = self.partner_shipping_id.name
            # note.text = self.note
        doc_currency = etree.SubElement(parent_node, ns["cbc"] + currency_node_name)
        doc_currency.text = self.currency_id.name

    def _ubl_add_monetary_total(self, parent_node, ns, version="2.1"):
        monetary_total = etree.SubElement(
            parent_node, ns["cac"] + "AnticipatedMonetaryTotal"
        )
        line_total = etree.SubElement(
            monetary_total,
            ns["cbc"] + "LineExtensionAmount",
            currencyID=self.currency_id.name,
        )
        line_total.text = str(self.amount_untaxed)
        payable_amount = etree.SubElement(
            monetary_total,
            ns["cbc"] + "PayableAmount",
            currencyID=self.currency_id.name,
        )
        payable_amount.text = str(self.amount_total)

    def _ubl_add_rfq_line(self, parent_node, oline, line_number, ns, version="2.1"):
        line_root = etree.SubElement(parent_node, ns["cac"] + "RequestForQuotationLine")
        self._ubl_add_line_item(
            line_number,
            oline,
            oline.name,
            oline.product_id,
            "sale",
            oline.product_qty,
            oline.product_uom,
            line_root,
            ns,
            seller=self.partner_id.commercial_partner_id,
            version=version,
        )

    def _ubl_add_order_line(self, parent_node, oline, line_number, ns, version="2.1"):
        line_root = etree.SubElement(parent_node, ns["cac"] + "OrderLine")
        dpo = self.env["decimal.precision"]
        qty_precision = dpo.precision_get("Product Unit of Measure")
        price_precision = dpo.precision_get("Product Price")
        self._ubl_add_line_item(
            line_number,
            oline,
            oline.name,
            oline.product_id,
            "sale",
            oline.product_qty,
            oline.product_uom,
            line_root,
            ns,
            seller=self.partner_id.commercial_partner_id,
            currency=self.currency_id,
            price_subtotal=oline.price_subtotal,
            qty_precision=qty_precision,
            price_precision=price_precision,
            version=version,
        )

    @api.model
    def _ubl_add_line_item(
        self,
        line_number,
        sale_line,
        name,
        product,
        type_,
        quantity,
        uom,
        parent_node,
        ns,
        seller=False,
        currency=False,
        price_subtotal=False,
        qty_precision=3,
        price_precision=2,
        version="2.1",
    ):
        line_item = etree.SubElement(parent_node, ns["cac"] + "LineItem")
        line_item_id = etree.SubElement(line_item, ns["cbc"] + "ID")
        line_item_id.text = str(line_number)
        if not uom.unece_code:
            raise UserError(
                _("Missing UNECE code on unit of measure '%(uom)s'", uom=uom.name)
            )
        quantity_node = etree.SubElement(
            line_item, ns["cbc"] + "Quantity", unitCode=uom.unece_code
        )
        quantity_node.text = str(quantity)
        if currency and price_subtotal:
            line_backorder_qty = etree.SubElement(
                line_item, ns["cbc"] + "MaximumBackorderQuantity", unitCode="C62"
            )
            backorder_qty = (
                0
                if sale_line.product_uom_qty - sale_line.qty_delivered < 0
                else sale_line.product_uom_qty - sale_line.qty_delivered
            )
            line_backorder_qty.text = str(backorder_qty)

            line_amount = etree.SubElement(
                line_item, ns["cbc"] + "LineExtensionAmount", currencyID=currency.name
            )
            line_amount.text = str(price_subtotal)

            line_delivery_root = etree.SubElement(line_item, ns["cac"] + "Delivery")
            line_delivery_qty = etree.SubElement(
                line_delivery_root, ns["cbc"] + "Quantity", unitCode="C62"
            )
            line_delivery_qty.text = str(sale_line.qty_delivered)

            actual_delivery_date = ""
            if sale_line.order_id.effective_date:
                actual_delivery_date = fields.Datetime.to_string(
                    sale_line.order_id.effective_date
                )

            line_delivery_date = etree.SubElement(
                line_delivery_root, ns["cbc"] + "ActualDeliveryDate"
            )
            line_delivery_date.text = str(actual_delivery_date)

            price_unit = 0.0
            # Use price_subtotal/qty to compute price_unit to be sure
            # to get a *tax_excluded* price unit
            if not float_is_zero(quantity, precision_digits=qty_precision):
                price_unit = float_round(
                    price_subtotal / float(quantity), precision_digits=price_precision
                )
            price = etree.SubElement(line_item, ns["cac"] + "Price")
            price_amount = etree.SubElement(
                price, ns["cbc"] + "PriceAmount", currencyID=currency.name
            )
            price_amount.text = str(price_unit)

            price_type = etree.SubElement(price, ns["cbc"] + "PriceType")
            price_type.text = "0"

            base_qty = etree.SubElement(
                price, ns["cbc"] + "BaseQuantity", unitCode=uom.unece_code
            )
            base_qty.text = "1"  # What else could it be ?
        self._ubl_add_item(
            name, product, line_item, ns, type_=type_, seller=seller, version=version
        )

    def get_delivery_partner(self):
        self.ensure_one()
        if self.partner_shipping_id:
            return self.partner_shipping_id
        return self.company_id.partner_id

    def cron_export_order_response_ubl_file(self):
        sale_orders = self.env["sale.order"].search(
            [
                ("ubl_export_done", "=", False),
                ("state", "=", "sale"),
            ]
        )
        sale_orders = sale_orders.filtered(
            lambda s: s.partner_id.default_ubl_import_partner is True
        )
        for sale in sale_orders:
            sale.export_order_response_ubl_file()

    def export_order_response_ubl_file(self):
        self.ensure_one()
        attach_values = self._get_ubl_xml_attachment_values()
        if attach_values:
            ubl_file_path = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("order_response_export_ubl.path")
            )

            if ubl_file_path:
                file_path = Path(f"{ubl_file_path}ORDRSP_{str(self.id)}.xml")
                with open(file_path, "wb") as file:
                    file.write(attach_values["xml_string"])

            filename_xml = f"ORDRSP_{str(self.id)}.xml"
            attachment_values = {
                "name": filename_xml,
                "type": "binary",
                "datas": base64.b64encode(attach_values["xml_string"]),
                "mimetype": "application/xml",
                "res_model": "sale.order",
                "res_id": self.id,
            }
            attachment = self.env["ir.attachment"].create(attachment_values)
        self.ubl_export_done = True
        return attachment

    def generate_rfq_ubl_xml_etree(self, version="2.1"):
        nsmap, ns = self._ubl_get_nsmap_namespace(
            "RequestForQuotation-2", version=version
        )
        xml_root = etree.Element("RequestForQuotation", nsmap=nsmap)
        doc_type = "rfq"
        self._ubl_add_header(doc_type, xml_root, ns, version=version)

        # The order of SellerSupplierParty / BuyerCustomerParty is different
        # between RFQ and Order !
        self._ubl_add_supplier_party(
            self.partner_id, False, "SellerSupplierParty", xml_root, ns, version=version
        )
        if version == "2.1":
            self._ubl_add_customer_party(
                False,
                self.company_id,
                "BuyerCustomerParty",
                xml_root,
                ns,
                version=version,
            )
        delivery_partner = self.get_delivery_partner()
        self._ubl_add_delivery(delivery_partner, xml_root, ns, version=version)
        if self.incoterm:
            self._ubl_add_delivery_terms(self.incoterm, xml_root, ns, version=version)

        for oline in self.order_line:
            # line_number as third arg comes from sale.order.line id field
            # see https://github.com/OCA/edi/issues/300
            self._ubl_add_rfq_line(xml_root, oline, oline.id, ns, version=version)
        return xml_root

    def generate_order_ubl_xml_etree(self, version="2.1"):
        nsmap, ns = self._ubl_get_nsmap_namespace("OrderResponse-2", version=version)
        # nsmap, ns = self._ubl_get_nsmap_namespace("Order-2", version=version)
        # xml_root = etree.Element("Order", nsmap=nsmap)
        xml_root = etree.Element("OrderResponse", nsmap=nsmap)
        doc_type = "order"
        self._ubl_add_header(doc_type, xml_root, ns, version=version)

        if self.client_order_ref:
            self._ubl_add_order_reference(
                self.client_order_ref, xml_root, ns, version=version
            )

        self._ubl_add_buyer_customer_party(
            self.partner_id, False, "BuyerCustomerParty", xml_root, ns, version=version
        )

        if self.partner_id.edicode:
            originator_party_root = etree.SubElement(
                xml_root, ns["cac"] + "OriginatorCustomerParty"
            )
            party_root = etree.SubElement(originator_party_root, ns["cac"] + "Party")
            party_identification_root = etree.SubElement(
                party_root, ns["cac"] + "PartyIdentification"
            )
            party_identification = etree.SubElement(
                party_identification_root, ns["cbc"] + "ID"
            )
            party_identification.text = str(self.partner_id.edicode)

        self._ubl_add_supplier_party(
            False, self.company_id, "SellerSupplierParty", xml_root, ns, version=version
        )

        #        self._ubl_add_customer_party(
        #           False, self.company_id, "BuyerCustomerParty",
        #           xml_root, ns, version=version
        #        )
        #        self._ubl_add_supplier_party(
        #           self.partner_id, False, "SellerSupplierParty",
        #           xml_root, ns, version=version
        #        )
        delivery_partner = self.get_delivery_partner()
        self._ubl_add_order_delivery(delivery_partner, xml_root, ns, version=version)
        if self.incoterm:
            self._ubl_add_delivery_terms(self.incoterm, xml_root, ns, version=version)
        if self.payment_term_id:
            self._ubl_add_payment_terms(
                self.payment_term_id, xml_root, ns, version=version
            )
        self._ubl_add_monetary_total(xml_root, ns, version=version)

        for oline in self.order_line:
            # line_number as third arg comes from sale.order.line id field
            # see https://github.com/OCA/edi/issues/300
            self._ubl_add_order_line(xml_root, oline, oline.id, ns, version=version)
        return xml_root

    def generate_ubl_xml_string(self, doc_type, version="2.1"):
        """Provide UBL Xml string with no check
        According to your use check this string integrity with
        _ubl_check_xml_schema() method
        """
        self.ensure_one()
        xml_root = self.get_ubl_xml_etree(doc_type, version=version)
        xml_string = etree.tostring(
            xml_root, pretty_print=True, encoding="UTF-8", xml_declaration=True
        )
        logger.debug(
            "%(doc_type)s UBL XML file generated "
            "for sale order ID %(id)d (state %(state)s)",
            doc_type=doc_type,
            id=self.id,
            state=self.state,
        )
        logger.debug(xml_string)
        return xml_string

    def get_ubl_xml_etree(self, doc_type, version="2.1"):
        self.ensure_one()
        if doc_type not in ("order", "rfq"):
            raise UserError(
                _(
                    "Wrong document type %(doc_type)s to generate UBL XML",
                    doc_type=doc_type,
                )
            )
        logger.debug("Starting to generate UBL XML %s file", doc_type)
        lang = self.get_ubl_lang()
        # The aim of injecting lang in context
        # is to have the content of the XML in the partner's lang
        # but the problem is that the error messages will also be in
        # that lang. But the error messages should almost never
        # happen except the first days of use, so it's probably
        # not worth the additional code to handle the 2 langs
        if doc_type == "order":
            xml_root = self.with_context(lang=lang).generate_order_ubl_xml_etree(
                version=version
            )
        elif doc_type == "rfq":
            xml_root = self.with_context(lang=lang).generate_rfq_ubl_xml_etree(
                version=version
            )
        return xml_root

    def get_document_name(self, doc_type):
        document = False
        if doc_type == "order":
            document = "Order"
        elif doc_type == "rfq":
            document = "RequestForQuotation"
        return document

    def get_ubl_filename(self, doc_type, version="2.1"):
        """This method is designed to be inherited"""
        if doc_type == "rfq":
            return f"UBL-RequestForQuotation-{version}-{self.name}.xml"
        elif doc_type == "order":
            return f"UBL-Order-{version}-{self.name}.xml"

    def get_ubl_version(self):
        return self.env.context.get("ubl_version", "2.1")

    def get_ubl_lang(self):
        self.ensure_one()
        return self.partner_id.lang or "en_US"

    def _get_ubl_xml_attachment_values(self):
        self.ensure_one()
        doc_type = self.get_ubl_sale_order_doc_type()
        if not doc_type:
            return {}
        version = self.get_ubl_version()
        return {
            "doc_type": doc_type,
            "version": version,
            "xml_filename": self.get_ubl_filename(doc_type, version=version),
            "xml_string": self.generate_ubl_xml_string(doc_type, version=version),
        }

    def _pdf_has_xml_attachment(self, pdf_content, xml_filename):
        return xml_filename in self.env["pdf.xml.tool"].pdf_get_xml_files(pdf_content)

    def _embed_ubl_xml_in_pdf_content(self, pdf_content, xml_filename, xml_string):
        if self._pdf_has_xml_attachment(pdf_content, xml_filename):
            return pdf_content
        return self.env["pdf.xml.tool"].pdf_embed_xml(
            pdf_content, xml_filename, xml_string
        )

    def add_xml_in_pdf_buffer(self, buffer):
        self.ensure_one()
        attach_values = self._get_ubl_xml_attachment_values()
        if attach_values:
            pdf_content = self._embed_ubl_xml_in_pdf_content(
                buffer.getvalue(),
                attach_values["xml_filename"],
                attach_values["xml_string"],
            )
            buffer.close()
            buffer = BytesIO(pdf_content)
        return buffer

    def embed_ubl_xml_in_pdf(self, pdf_content, pdf_file=None):
        self.ensure_one()
        attach_values = self._get_ubl_xml_attachment_values()
        if attach_values:
            self._ubl_check_xml_schema(
                attach_values["xml_string"],
                self.get_document_name(attach_values["doc_type"]),
                version=attach_values["version"],
            )
            pdf_content = self._embed_ubl_xml_in_pdf_content(
                pdf_content, attach_values["xml_filename"], attach_values["xml_string"]
            )
        return pdf_content

    def get_ubl_sale_order_doc_type(self):
        self.ensure_one()
        doc_type = False
        # if self.state in self.get_rfq_states():
        #    doc_type = "rfq"
        # elif self.state == "sale":
        doc_type = "order"
        return doc_type

    @api.model
    def _ubl_get_party_identification(self, commercial_partner):
        values = {}

        if commercial_partner.edicode:
            values = {
                "edicode": commercial_partner.edicode,
            }

        return values

    @api.model
    def _ubl_add_order_reference(self, reference, parent_node, ns, version="2.1"):
        reference_root = etree.SubElement(parent_node, ns["cac"] + "OrderReference")

        reference_name = etree.SubElement(reference_root, ns["cbc"] + "ID")
        reference_name.text = reference

        order_name = etree.SubElement(reference_root, ns["cbc"] + "SalesOrderID")
        order_name.text = self.name

        date = self.date_order
        date = fields.Date.to_string(date)
        issue_date = etree.SubElement(reference_root, ns["cbc"] + "IssueDate")
        issue_date.text = date

    @api.model
    def _ubl_add_buyer_customer_party(
        self, partner, company, node_name, parent_node, ns, version="2.1"
    ):
        """Please read the docstring of the method _ubl_add_supplier_party"""
        if company:
            if partner:
                assert (
                    partner.commercial_partner_id == company.partner_id
                ), "partner is wrong"
            else:
                partner = company.partner_id
        customer_party_root = etree.SubElement(parent_node, ns["cac"] + node_name)
        partner_ref = self._ubl_get_customer_assigned_id(partner)
        if partner_ref:
            customer_ref = etree.SubElement(
                customer_party_root, ns["cbc"] + "SupplierAssignedAccountID"
            )
            customer_ref.text = partner_ref
        self._ubl_add_party_to_buyer_customer(
            partner, company, "Party", customer_party_root, ns, version=version
        )
        # TODO: rewrite support for AccountingContact + add DeliveryContact
        # Additional optional args
        return customer_party_root

    @api.model
    def _ubl_add_party_to_buyer_customer(
        self, partner, company, node_name, parent_node, ns, version="2.1"
    ):
        commercial_partner = partner.commercial_partner_id
        party = etree.SubElement(parent_node, ns["cac"] + node_name)
        if commercial_partner.website:
            website = etree.SubElement(party, ns["cbc"] + "WebsiteURI")
            website.text = commercial_partner.website
        self._ubl_add_party_identification(
            commercial_partner, party, ns, version=version
        )
        party_name = etree.SubElement(party, ns["cac"] + "PartyName")
        name = etree.SubElement(party_name, ns["cbc"] + "Name")
        name.text = commercial_partner.name
        if partner.lang:
            self._ubl_add_language(partner.lang, party, ns, version=version)
        self._ubl_add_address(partner, "PostalAddress", party, ns, version=version)
        self._ubl_add_party_tax_scheme(commercial_partner, party, ns, version=version)
        if commercial_partner.is_company or company:
            self._ubl_add_party_legal_entity(
                commercial_partner, party, ns, version="2.1"
            )

    @api.model
    def _ubl_add_order_delivery(self, delivery_partner, parent_node, ns, version="2.1"):
        delivery = etree.SubElement(parent_node, ns["cac"] + "Delivery")
        delivery_location = etree.SubElement(delivery, ns["cac"] + "DeliveryLocation")
        self._ubl_add_address(
            delivery_partner, "Address", delivery_location, ns, version=version
        )
        self._ubl_add_order_party(
            delivery_partner, False, "DeliveryParty", delivery, ns, version=version
        )

    @api.model
    def _ubl_add_order_party(
        self, partner, company, node_name, parent_node, ns, version="2.1"
    ):
        commercial_partner = partner.commercial_partner_id
        party = etree.SubElement(parent_node, ns["cac"] + node_name)
        if commercial_partner.website:
            website = etree.SubElement(party, ns["cbc"] + "WebsiteURI")
            website.text = commercial_partner.website
        self._ubl_add_party_identification(
            self.company_id.partner_id, party, ns, version=version
        )
        party_name = etree.SubElement(party, ns["cac"] + "PartyName")
        name = etree.SubElement(party_name, ns["cbc"] + "Name")
        name.text = commercial_partner.name
        if partner.lang:
            self._ubl_add_language(partner.lang, party, ns, version=version)
        self._ubl_add_address(partner, "PostalAddress", party, ns, version=version)
        self._ubl_add_party_tax_scheme(commercial_partner, party, ns, version=version)
        if commercial_partner.is_company or company:
            self._ubl_add_party_legal_entity(
                commercial_partner, party, ns, version="2.1"
            )
        self._ubl_add_contact(partner, party, ns, version=version)
