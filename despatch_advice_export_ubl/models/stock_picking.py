import logging
from io import BytesIO

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import UserError

logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "base.ubl"]

    def _ubl_add_header(self, parent_node, ns, version="2.1"):
        now_utc = fields.Datetime.to_string(fields.Datetime.now())
        date = now_utc[:10]
        time = now_utc[11:]
#        currency_node_name = "PricingCurrencyCode"
        ubl_version = etree.SubElement(parent_node, ns["cbc"] + "UBLVersionID")
        ubl_version.text = version
        doc_id = etree.SubElement(parent_node, ns["cbc"] + "ID")
        doc_id.text = self.name
        issue_date = etree.SubElement(parent_node, ns["cbc"] + "IssueDate")
        issue_date.text = date
        if self.partner_id:
            note = etree.SubElement(parent_node, ns["cbc"] + "Note")
            note.text = self.partner_id.name

    def get_ubl_filename(self, version="2.1"):
        """This method is designed to be inherited"""
        return f"UBL-Despatch-Advice-{version}-{self.name}.xml"

    def get_ubl_version(self):
        return self.env.context.get("ubl_version", "2.1")

    def get_ubl_lang(self):
        self.ensure_one()
        return self.partner_id.lang or "en_US"

    def _get_ubl_xml_attachment_values(self):
        self.ensure_one()
#        doc_type = self.get_ubl_sale_order_doc_type()
#        if not doc_type:
#            return {}
        version = self.get_ubl_version()
        filename = self.get_ubl_filename(version=version)
#        print("FILENAME", filename)
        return {
#            "doc_type": doc_type,
            "version": version,
            "xml_filename": filename,
            "xml_string": self.generate_ubl_xml_string(version=version),
        }

    def _pdf_has_xml_attachment(self, pdf_content, xml_filename):
        return xml_filename in self.env["pdf.xml.tool"].pdf_get_xml_files(pdf_content)

    def _embed_ubl_xml_in_pdf_content(self, pdf_content, xml_filename, xml_string):
#        print("PDF content", pdf_content)
        if self._pdf_has_xml_attachment(pdf_content, xml_filename):
            return pdf_content
        return self.env["pdf.xml.tool"].pdf_embed_xml(
            pdf_content, xml_filename, xml_string
        )

    def add_xml_in_pdf_buffer(self, buffer):
        self.ensure_one()
        attach_values = self._get_ubl_xml_attachment_values()
#        print("ATTACH VALUES", attach_values)
        if attach_values:
            pdf_content = self._embed_ubl_xml_in_pdf_content(
                buffer.getvalue(),
                attach_values["xml_filename"],
                attach_values["xml_string"],
            )
            buffer.close()
            buffer = BytesIO(pdf_content)
        return buffer

    def generate_ubl_xml_string(self, version="2.1"):
        """Provide UBL Xml string with no check
        According to your use check this string integrity with
        _ubl_check_xml_schema() method
        """
        self.ensure_one()
        xml_root = self.get_ubl_xml_etree(version=version)
        xml_string = etree.tostring(
            xml_root, pretty_print=True, encoding="UTF-8", xml_declaration=True
        )
#        print("XML STRING", xml_string)
        logger.debug(
            "UBL XML file generated "
            "for stock picking ID %(id)d",
            id=self.id,
        )
        logger.debug(xml_string)
        return xml_string

    def get_ubl_xml_etree(self, version="2.1"):
        self.ensure_one()
        logger.debug("Starting to generate UBL XML file")
        lang = self.get_ubl_lang()
        xml_root = self.with_context(lang=lang).generate_picking_ubl_xml_etree(
            version=version
        )
        return xml_root

    def generate_picking_ubl_xml_etree(self, version="2.1"):
        nsmap, ns = self._ubl_get_nsmap_namespace("CommonExtensionComponents-2", version=version)
        xml_root = etree.Element("DespatchAdvice", nsmap=nsmap)
#        doc_type = "order"
        self._ubl_add_header(xml_root, ns, version=version)

        if self.sale_id:
            self._ubl_add_order_reference(
                self.sale_id, xml_root, ns, version=version
            )

        partner = self.partner_id
        despatch_supplier_party_root = etree.SubElement(xml_root, ns["cac"] + "DespatchSupplierParty")
#        partner_ref = self._ubl_get_customer_assigned_id(partner)
#        if partner_ref:
#            customer_ref = etree.SubElement(
#                delivery_customer_party_root, ns["cbc"] + "SupplierAssignedAccountID"
#            )
#            customer_ref.text = partner_ref
        self._ubl_add_delivery_party(
            self.company_id.partner_id, False, "Party", despatch_supplier_party_root, ns, version=version
        )

        delivery_customer_party_root = etree.SubElement(xml_root, ns["cac"] + "DeliveryCustomerParty")

        self._ubl_add_delivery_customer_party(
            self.partner_id, False, "Party", delivery_customer_party_root, ns, version=version
        )

        buyer_customer_party_root = etree.SubElement(xml_root, ns["cac"] + "BuyerCustomerParty")

        self._ubl_add_buyer_customer_party(
            self.partner_id, False, "Party", buyer_customer_party_root, ns, version=version
        )

        seller_supplier_party_root = etree.SubElement(xml_root, ns["cac"] + "SellerSupplierParty")

#        self._ubl_add_supplier_party(
        self._ubl_add_delivery_customer_party(
            self.company_id.partner_id, False, "Party", seller_supplier_party_root, ns, version=version
        )
#            False, self.company_id, "SellerSupplierParty", xml_root, ns, version=version

#            False, self.company_id, "Party", seller_supplier_party_root, ns, version=version
#            self.company_id.partner_id, False, "Party", seller_supplier_party_root, ns, version=version

#            self.company_id.partner_id, self.company_id, "Party", delivery_customer_party_root, ns, version=version
#        self._ubl_add_party_identification(
#            self.company_id.partner_id, delivery_customer_party_root, ns, version=version
#        )

# 6     def _ubl_add_party_identification(
#  5         self, commercial_partner, parent_node, ns, version="2.1"
#  4     ):







#        country = self.partner_id.country_id and self.partner_id.country_id
#        if country:
#            self._ubl_add_country(country, xml_root, ns, version=version)
#        self.ubl_parse_delivery(xml_root, ns)
        #country, parent_node, ns, version="2.1"

        #self._ubl_add_customer_party(
        #    self.partner_id, False, "BuyerCustomerParty", xml_root, ns, version=version
        #)
        #self._ubl_add_supplier_party(
        #    False, self.company_id, "SellerSupplierParty", xml_root, ns, version=version
        #)

        ##        self._ubl_add_customer_party(
        ##           False, self.company_id, "BuyerCustomerParty",
        ##           xml_root, ns, version=version
        ##        )
        ##        self._ubl_add_supplier_party(
        ##           self.partner_id, False, "SellerSupplierParty",
        ##           xml_root, ns, version=version
        ##        )
        #delivery_partner = self.get_delivery_partner()
        #self._ubl_add_delivery(delivery_partner, xml_root, ns, version=version)
        #if self.incoterm:
        #    self._ubl_add_delivery_terms(self.incoterm, xml_root, ns, version=version)
        #if self.payment_term_id:
        #    self._ubl_add_payment_terms(
        #        self.payment_term_id, xml_root, ns, version=version
        #    )
        #self._ubl_add_monetary_total(xml_root, ns, version=version)

        #for oline in self.order_line:
        #    # line_number as third arg comes from sale.order.line id field
        #    # see https://github.com/OCA/edi/issues/300
        #    self._ubl_add_order_line(xml_root, oline, oline.id, ns, version=version)
        return xml_root

    @api.model
    def _ubl_add_order_reference(self, sale, parent_node, ns, version="2.1"):
        reference = sale.client_order_ref
        reference_root = etree.SubElement(parent_node, ns["cac"] + "OrderReference")

        reference_name = etree.SubElement(reference_root, ns["cbc"] + "ID")
        reference_name.text = reference

        order_name = etree.SubElement(reference_root, ns["cbc"] + "SalesOrderID")
        order_name.text = sale.name

        date = sale.date_order
        date = fields.Date.to_string(date)
        issue_date = etree.SubElement(reference_root, ns["cbc"] + "IssueDate")
        issue_date.text = date

    @api.model
    def _ubl_add_delivery_party(
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

    @api.model
    def _ubl_add_buyer_customer_party(
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
        name.text = "null"
        #name.text = commercial_partner.name

    @api.model
    def _ubl_add_delivery_customer_party(
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
#        if partner.lang:
#            self._ubl_add_language(partner.lang, party, ns, version=version)
        self._ubl_add_address(partner, "PostalAddress", party, ns, version=version)
#        self._ubl_add_party_tax_scheme(commercial_partner, party, ns, version=version)
#        if commercial_partner.is_company or company:
#            self._ubl_add_party_legal_entity(
#                commercial_partner, party, ns, version="2.1"
#            )
#        self._ubl_add_contact(partner, party, ns, version=version)

    @api.model
    def _ubl_get_party_identification(self, commercial_partner):
        values = {}

        if commercial_partner.edicode:
            values = {
                "edicode": commercial_partner.edicode,
            }

        print("VALUES", values)

        return values
