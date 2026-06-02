import base64
import logging
from io import BytesIO
from pathlib import Path

from lxml import etree

from odoo import api, fields, models

logger = logging.getLogger(__name__)


class StockPicking(models.Model):
    _name = "stock.picking"
    _inherit = ["stock.picking", "base.ubl"]

    ubl_export_done = fields.Boolean(default=False)

    def _ubl_add_header(self, parent_node, ns, version="2.1"):
        now_utc = fields.Datetime.to_string(fields.Datetime.now())
        date = now_utc[:10]
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
        version = self.get_ubl_version()
        filename = self.get_ubl_filename(version=version)
        return {
            "version": version,
            "xml_filename": filename,
            "xml_string": self.generate_ubl_xml_string(version=version),
        }

    def _pdf_has_xml_attachment(self, pdf_content, xml_filename):
        return xml_filename in self.env["pdf.xml.tool"].pdf_get_xml_files(pdf_content)

    def _embed_ubl_xml_in_pdf_content(self, pdf_content, xml_filename, xml_string):
        if self._pdf_has_xml_attachment(pdf_content, xml_filename):
            return pdf_content
        return self.env["pdf.xml.tool"].pdf_embed_xml(
            pdf_content, xml_filename, xml_string
        )

    def cron_export_despatch_advice_ubl_file(self):
        pickings = self.env["stock.picking"].search(
            [
                ("ubl_export_done", "=", False),
                ("state", "=", "done"),
            ]
        )
        pickings = pickings.filtered(
            lambda p: p.sale_id
            and p.sale_id.partner_id.default_ubl_import_partner is True
        )
        for picking in pickings:
            picking.export_despatch_advice_ubl_file()

    def export_despatch_advice_ubl_file(self):
        self.ensure_one()
        attach_values = self._get_ubl_xml_attachment_values()
        if attach_values:
            ubl_file_path = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param("despatch_advice_export_ubl.path")
            )

            if ubl_file_path:
                file_path = Path(f"{ubl_file_path}DESADV_{str(self.id)}.xml")
                with open(file_path, "wb") as file:
                    file.write(attach_values["xml_string"])

            filename_xml = f"DESADV_{str(self.id)}.xml"
            attachment_values = {
                "name": filename_xml,
                "type": "binary",
                "datas": base64.b64encode(attach_values["xml_string"]),
                "mimetype": "application/xml",
                "res_model": "stock.picking",
                "res_id": self.id,
            }
            attachment = self.env["ir.attachment"].create(attachment_values)
        self.ubl_export_done = True
        return attachment

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
        logger.debug(
            "UBL XML file generated " "for stock picking ID %(id)d",
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
        nsmap, ns = self._ubl_get_nsmap_namespace(
            "CommonExtensionComponents-2", version=version
        )
        xml_root = etree.Element("DespatchAdvice", nsmap=nsmap)
        self._ubl_add_header(xml_root, ns, version=version)

        if self.sale_id:
            self._ubl_add_order_reference(self.sale_id, xml_root, ns, version=version)

        # partner = self.partner_id
        despatch_supplier_party_root = etree.SubElement(
            xml_root, ns["cac"] + "DespatchSupplierParty"
        )

        self._ubl_add_delivery_party(
            self.company_id.partner_id,
            False,
            "Party",
            despatch_supplier_party_root,
            ns,
            version=version,
        )

        delivery_customer_party_root = etree.SubElement(
            xml_root, ns["cac"] + "DeliveryCustomerParty"
        )

        self._ubl_add_delivery_customer_party(
            True,
            self.partner_id,
            False,
            "Party",
            delivery_customer_party_root,
            ns,
            version=version,
        )

        buyer_customer_party_root = etree.SubElement(
            xml_root, ns["cac"] + "BuyerCustomerParty"
        )

        self._ubl_add_buyer_customer_party(
            self.partner_id,
            False,
            "Party",
            buyer_customer_party_root,
            ns,
            version=version,
        )

        seller_supplier_party_root = etree.SubElement(
            xml_root, ns["cac"] + "SellerSupplierParty"
        )

        self._ubl_add_delivery_customer_party(
            False,
            self.company_id.partner_id,
            False,
            "Party",
            seller_supplier_party_root,
            ns,
            version=version,
        )

        shipment_root = etree.SubElement(xml_root, ns["cac"] + "Shipment")

        picking_name = etree.SubElement(shipment_root, ns["cbc"] + "ID")
        picking_name.text = self.name

        picking_net_weight = 0
        picking_gross_weight = 0
        for move in self.move_ids:
            picking_net_weight += move.product_qty * move.product_id.net_weight
            picking_gross_weight += move.product_qty * move.product_id.weight

        gross_weight_sum_picking = etree.SubElement(
            shipment_root, ns["cbc"] + "GrossWeightMeasure", unitCode="KG"
        )
        gross_weight_sum_picking.text = str(picking_gross_weight)

        net_weight_sum_picking = etree.SubElement(
            shipment_root, ns["cbc"] + "NetWeightMeasure", unitCode="KG"
        )
        net_weight_sum_picking.text = str(picking_net_weight)

        volume_sum_picking = etree.SubElement(
            shipment_root, ns["cbc"] + "GrossVolumeMeasure", unitCode="MTQ"
        )
        volume_sum_picking.text = str(self.volume)

        total_transport_qty_picking = etree.SubElement(
            shipment_root, ns["cbc"] + "TotalTransportHandlingUnitQuantity"
        )
        total_transport_qty_picking.text = str(0.00)

        goods_item_picking = etree.SubElement(shipment_root, ns["cac"] + "GoodsItem")

        goods_item_gross_weight = etree.SubElement(
            goods_item_picking, ns["cbc"] + "GrossWeightMeasure", unitCode="KG"
        )
        goods_item_gross_weight.text = str(0.00)

        goods_item_net_weight = etree.SubElement(
            goods_item_picking, ns["cbc"] + "NetWeightMeasure", unitCode="KG"
        )
        goods_item_net_weight.text = str(0.00)

        goods_item_volume = etree.SubElement(
            goods_item_picking, ns["cbc"] + "GrossVolumeMeasure", unitCode="MTQ"
        )
        goods_item_volume.text = str(0.00)

        for move in self.move_ids:
            despatch_root = etree.SubElement(xml_root, ns["cac"] + "DespatchLine")
            despatch_id = etree.SubElement(despatch_root, ns["cbc"] + "ID")
            despatch_id.text = move.picking_id.name
            despatch_delivered = etree.SubElement(
                despatch_root, ns["cbc"] + "DeliveredQuantity", unitCode="C62"
            )
            despatch_delivered.text = str(move.quantity)
            despatch_outstanding = etree.SubElement(
                despatch_root, ns["cbc"] + "OutstandingQuantity", unitCode="C62"
            )
            outstanding_qty = (
                0
                if move.product_uom_qty - move.quantity < 0
                else move.product_uom_qty - move.quantity
            )
            despatch_outstanding.text = str(outstanding_qty)
            despatch_oversupply = etree.SubElement(
                despatch_root, ns["cbc"] + "OversupplyQuantity", unitCode="C62"
            )
            despatch_oversupply.text = str(abs(move.product_uom_qty - move.quantity))

            sale_line = move.sale_line_id
            sale_line_root = etree.SubElement(
                despatch_root, ns["cac"] + "OrderLineReference"
            )
            line_id = etree.SubElement(sale_line_root, ns["cbc"] + "LineID")
            line_id.text = str(sale_line.id)
            sale_line_id = etree.SubElement(
                sale_line_root, ns["cbc"] + "SalesOrderLineID"
            )
            sale_line_id.text = str(sale_line.sequence)
            if sale_line.order_id.client_order_ref:
                order_root = etree.SubElement(
                    sale_line_root, ns["cbc"] + "OrderReference"
                )
                order_id = etree.SubElement(order_root, ns["cbc"] + "ID")
                order_id.text = sale_line.order_id.client_order_ref
                sales_order_id = etree.SubElement(
                    order_root, ns["cbc"] + "SalesOrderID"
                )
                sales_order_id.text = sale_line.order_id.client_order_ref

            # self._ubl_add_item(
            #   move.name, move.product_id, despatch_root,
            #   ns, type_="sale", seller=False, version=version
            # )

            item_root = etree.SubElement(despatch_root, ns["cac"] + "Item")
            item_desc = etree.SubElement(item_root, ns["cbc"] + "Description")
            item_desc.text = move.product_id.name
            item_name = etree.SubElement(item_root, ns["cbc"] + "Name")
            item_name.text = move.product_id.name
            seller_item_root = etree.SubElement(
                item_root, ns["cac"] + "SellersItemIdentification"
            )
            seller_item_id = etree.SubElement(seller_item_root, ns["cbc"] + "ID")
            seller_item_id.text = str(move.product_id.default_code)
            standard_item_root = etree.SubElement(
                item_root, ns["cac"] + "StandardItemIdentification"
            )
            standard_item_id = etree.SubElement(standard_item_root, ns["cbc"] + "ID")
            standard_item_id.text = str(move.product_id.barcode)
            country_item_root = etree.SubElement(item_root, ns["cac"] + "OriginCountry")
            country_item_id = etree.SubElement(
                country_item_root, ns["cbc"] + "IdentificationCode"
            )
            country_item_id.text = str(move.product_id.origin_country_id.code)

            taxes = sale_line.tax_id
            skip_taxes = self.env.context.get("ubl_add_item__skip_taxes")
            if taxes and not skip_taxes:
                for tax in taxes:
                    classified_tax_root = etree.SubElement(
                        item_root, ns["cac"] + "ClassifiedTaxCategory"
                    )
                    classified_tax_id = etree.SubElement(
                        classified_tax_root, ns["cbc"] + "ID"
                    )
                    classified_tax_id.text = str(tax.unece_categ_code)
                    classified_tax_name = etree.SubElement(
                        classified_tax_root, ns["cbc"] + "Name"
                    )
                    classified_tax_name.text = str(tax.name)
                    classified_tax_percent = etree.SubElement(
                        classified_tax_root, ns["cbc"] + "Percent"
                    )
                    classified_tax_percent.text = str(tax.amount)
                    base_unit_measure = etree.SubElement(
                        classified_tax_root,
                        ns["cbc"] + "BaseUnitMeasure",
                        unitCode="C62",
                    )
                    base_unit_measure.text = str(sale_line.price_unit)
                    currency_name = sale_line.order_id.currency_id.name
                    base_unit_measure = etree.SubElement(
                        classified_tax_root,
                        ns["cbc"] + "PerUnitAmount",
                        currencyID=str(currency_name),
                    )
                    base_unit_measure.text = str(sale_line.price_unit)
                    etree.SubElement(classified_tax_root, ns["cac"] + "TaxScheme")

            line_shipment_root = etree.SubElement(despatch_root, ns["cac"] + "Shipment")

            line_despatch_id = etree.SubElement(line_shipment_root, ns["cbc"] + "ID")
            line_despatch_id.text = move.picking_id.name

            line_gross_weight_sum_picking = etree.SubElement(
                line_shipment_root, ns["cbc"] + "GrossWeightMeasure", unitCode="KG"
            )
            line_gross_weight_sum_picking.text = str(picking_gross_weight)

            line_volume_sum_picking = etree.SubElement(
                line_shipment_root, ns["cbc"] + "GrossVolumeMeasure", unitCode="MTQ"
            )
            line_volume_sum_picking.text = str(self.volume)

            line_goods_item_root = etree.SubElement(
                line_shipment_root, ns["cac"] + "GoodsItem"
            )
            line_goods_item = etree.SubElement(line_goods_item_root, ns["cac"] + "Item")
            line_goods_item_spec = etree.SubElement(
                line_goods_item, ns["cac"] + "ItemSpecificationDocumentReference"
            )
            line_goods_item_id = etree.SubElement(
                line_goods_item_spec, ns["cbc"] + "ID"
            )
            line_goods_item_id.text = str(sale_line.order_id.client_order_ref)
            line_goods_item_doc_type = etree.SubElement(
                line_goods_item_spec, ns["cbc"] + "DocumentTypeCode"
            )
            line_goods_item_doc_type.text = "CT"

            line_shipment_delivery = etree.SubElement(
                line_shipment_root, ns["cac"] + "Delivery"
            )
            line_shipment_delivery_date = etree.SubElement(
                line_shipment_delivery, ns["cbc"] + "ActualDeliveryDate"
            )

            if move.picking_id.date_done:
                effective_date = fields.Datetime.to_string(move.picking_id.date_done)
            else:
                effective_date = ""
            line_shipment_delivery_date.text = effective_date

            line_transport_unit_root = etree.SubElement(
                line_shipment_root, ns["cac"] + "TransportHandlingUnit"
            )
            line_transport_trace = etree.SubElement(
                line_transport_unit_root, ns["cbc"] + "TraceID"
            )
            line_transport_trace.text = ""
            line_actual_package_root = etree.SubElement(
                line_transport_unit_root, ns["cac"] + "ActualPackage"
            )
            line_actual_package_quantity = etree.SubElement(
                line_actual_package_root, ns["cac"] + "Quantity", unitCode="COLL"
            )
            line_actual_package_quantity.text = ""

        return xml_root

    @api.model
    def _ubl_add_order_reference(self, sale, parent_node, ns, version="2.1"):
        reference = sale.client_order_ref or ""
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

        sale_order_partner_id = self.sale_id and self.sale_id.partner_id
        if sale_order_partner_id:
            self._ubl_add_party_identification(
                sale_order_partner_id, party, ns, version=version
            )
        party_name = etree.SubElement(party, ns["cac"] + "PartyName")
        name = etree.SubElement(party_name, ns["cbc"] + "Name")
        name.text = "null"
        # name.text = commercial_partner.name

    @api.model
    def _ubl_add_delivery_customer_party(
        self, use_customer, partner, company, node_name, parent_node, ns, version="2.1"
    ):
        commercial_partner = partner.commercial_partner_id
        party = etree.SubElement(parent_node, ns["cac"] + node_name)
        if commercial_partner.website:
            website = etree.SubElement(party, ns["cbc"] + "WebsiteURI")
            website.text = commercial_partner.website

        if use_customer:
            partner_identification = self.sale_id and self.sale_id.partner_id or False
        else:
            partner_identification = commercial_partner

        if partner_identification:
            self._ubl_add_party_identification(
                partner_identification, party, ns, version=version
            )
        party_name = etree.SubElement(party, ns["cac"] + "PartyName")
        name = etree.SubElement(party_name, ns["cbc"] + "Name")
        name.text = str(commercial_partner.name)
        self._ubl_add_address(partner, "PostalAddress", party, ns, version=version)

    @api.model
    def _ubl_add_address(self, partner, node_name, parent_node, ns, version="2.1"):
        address = etree.SubElement(parent_node, ns["cac"] + node_name)
        if partner.street and partner.street2:
            addstreetname = etree.SubElement(
                address, ns["cbc"] + "AdditionalStreetName"
            )
            addstreetname.text = partner.street2
        # if oca/partner-contact/partner_address_street3 is installed
        if hasattr(partner, "street3") and partner.street3:
            # In an address, the real street is usually put in the last field
            streetname = etree.SubElement(address, ns["cbc"] + "StreetName")
            if partner.street and partner.street2:
                # The first field is usually the Department
                department = etree.SubElement(address, ns["cbc"] + "Department")
                department.text = partner.street
                streetname.text = partner.street2
                addstreetname.text = partner.street3
            elif partner.street or partner.street2:
                addstreetname = etree.SubElement(
                    address, ns["cbc"] + "AdditionalStreetName"
                )
                addstreetname.text = partner.street3
            else:
                streetname = etree.SubElement(address, ns["cbc"] + "StreetName")
                streetname.text = partner.street3
        if partner.city:
            city = etree.SubElement(address, ns["cbc"] + "CityName")
            city.text = partner.city
        if partner.zip:
            zip_code = etree.SubElement(address, ns["cbc"] + "PostalZone")
            zip_code.text = partner.zip
        if partner.street or partner.street2:
            address_line_root = etree.SubElement(address, ns["cac"] + "AddressLine")

            line_root = etree.SubElement(address_line_root, ns["cbc"] + "Line")
            line_root.text = partner.street or partner.street2
        if partner.state_id:
            state = etree.SubElement(address, ns["cbc"] + "CountrySubentity")
            state.text = partner.state_id.name
            state_code = etree.SubElement(address, ns["cbc"] + "CountrySubentityCode")
            state_code.text = partner.state_id.code
        if partner.country_id:
            self._ubl_add_country(partner.country_id, address, ns, version=version)
        else:
            logger.warning("UBL: missing country on partner %s", partner.name)

    @api.model
    def _ubl_get_party_identification(self, commercial_partner):
        values = {}

        if commercial_partner.edicode:
            values = {
                "edicode": commercial_partner.edicode,
            }

        return values
