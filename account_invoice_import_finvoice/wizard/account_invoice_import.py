# -*- coding: utf-8 -*-

import re
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime
from lxml import etree
import logging

logger = logging.getLogger(__name__)


def _to_float(string_number):
    # Format a '1 234,56' string as float 1234.56

    float_number = 0

    if not string_number:
        return float_number

    if type(string_number) is float:
        float_number = string_number
    else:
        if '.' in string_number and ',' in string_number:
            # TODO: Add support for comma as thousands separator (1,000.00)
            msg = _(
                'Using comma as thousands separator not supported! (%s)') \
                % string_number
            raise UserError(msg)

        # Replace comma with period
        string_number = string_number.replace(',', '.')

        # Replace non-numeric
        string_number = re.sub('[^0-9.]', '', string_number)

        float_number = float(string_number)

    return float_number


class AccountInvoiceImport(models.TransientModel):
    _name = 'account.invoice.import'
    _inherit = ['account.invoice.import', 'base.finvoice']

    @api.model
    def parse_xml_invoice(self, xml_root):
        if (xml_root.tag and xml_root.tag == 'Finvoice'):
            return self.parse_finvoice_invoice(xml_root)
        else:
            return super(AccountInvoiceImport, self).parse_xml_invoice(
                xml_root)

    def get_finvoice_attachments(self, xml_root, namespaces):
        attach_xpaths = xml_root.xpath(
            "./AttachmentDetails", namespaces=namespaces)
        attachments = {}

        for attach_xpath in attach_xpaths:

            filename_xpath = attach_xpath.xpath(
                "./AttachmentName", namespaces=namespaces)
            filename = filename_xpath and filename_xpath[0].text or False
            data_xpath = attach_xpath.xpath(
                "./AttachmentContent", namespaces=namespaces)

            data_base64 = data_xpath and data_xpath[0].text or False
            if filename and data_base64:
                if (
                        data_xpath[0].attrib and
                        data_xpath[0].attrib.get('mimeCode')):
                    mimetype = data_xpath[0].attrib['mimeCode'].split('/')
                    if len(mimetype) == 2:
                        filename = '%s.%s' % (filename, mimetype[1])
                attachments[filename] = data_base64
        return attachments

    def parse_finvoice_invoice_line(self, iline, counters, namespaces):
        price_unit_xpath = iline.xpath(
            "UnitPriceAmount", namespaces=namespaces)

        # TODO: assume DeliveredQuantity, or should we check InvoicedQuantity?

        qty_xpath = iline.xpath(
            "DeliveredQuantity", namespaces=namespaces)

        uom = {}
        if qty_xpath:
            qty = _to_float(qty_xpath[0].text)

            if qty_xpath[0].attrib:
                if qty_xpath[0].attrib.get("QuantityUnitCode"):
                    uom_name = qty_xpath[0].attrib["QuantityUnitCode"]
                    uom['name'] = uom_name

                if qty_xpath[0].attrib.get("QuantityUnitCodeUN"):
                    unece_code = qty_xpath[0].attrib["QuantityUnitCodeUN"]
                    uom['unece_code'] = unece_code

        product_dict = self.finvoice_parse_product(iline, namespaces)
        name_xpath = iline.xpath("ArticleName", namespaces=namespaces)
        name = name_xpath and name_xpath[0].text or '-'

        # Try to find the subtotal from RowVatExcludedAmount
        price_subtotal_xpath = iline.xpath(
            "./RowVatExcludedAmount", namespaces=namespaces)
        price_subtotal = price_subtotal_xpath and \
            _to_float(price_subtotal_xpath[0].text) or False

        if not price_subtotal:
            # RowVatExcludedAmount is not set
            # Try to find the subtotal from RowAmount
            price_subtotal_taxable_xpath = iline.xpath(
                "./RowAmount", namespaces=namespaces)
            price_subtotal_vat_xpath = iline.xpath(
                "./RowVatAmount", namespaces=namespaces)

            price_subtotal_taxable = _to_float(
                price_subtotal_taxable_xpath[0].text)
            price_subtotal_vat = _to_float(
                price_subtotal_vat_xpath[0].text)

            price_subtotal = _to_float(price_subtotal_taxable) \
                - _to_float(price_subtotal_vat)

        if not price_subtotal:
            return False

        # It seems UnitPriceAmount can be with or without tax, and it is
        #  not necessarily specified anywhere.
        #  Because of that, use subtotal/qty as unit price
        '''
        if price_unit_xpath:
            price_unit = _to_float(price_unit_xpath[0].text)
        else:
        '''
        price_unit = price_subtotal / qty
        counters['lines'] += price_subtotal
        taxes_xpath = iline.xpath("./RowVatRatePercent", namespaces=namespaces)

        if taxes_xpath:
            taxes = []
            tax_dict = {
                'amount_type': 'percent',
                'amount': _to_float(taxes_xpath[0].text) or 0.0,
                'price_include': False,  # The subtotal is given as untaxed
            }
            taxes.append(tax_dict)

        vals = {
            'product': product_dict,
            'qty': qty,
            'uom': uom,
            'price_unit': price_unit,
            'price_subtotal': price_subtotal,
            'name': name,
            'taxes': taxes,
        }

        return vals

    @api.model
    def parse_finvoice_invoice(self, xml_root):
        """Parse FINVOICE Invoice XML file."""
        namespaces = xml_root.nsmap

        logger.debug('XML file namespaces=%s', namespaces)
        xml_string = etree.tostring(
            xml_root, pretty_print=True, encoding='UTF-8',
            xml_declaration=True)
        finvoice_version = xml_root.attrib.get('Version') or '3.0'
        # Check XML schema to avoid headaches trying to import invalid files
        self._finvoice_check_xml_schema(xml_string, version=finvoice_version)

        doc_type_xpath = xml_root.xpath(
            "./InvoiceDetails/InvoiceTypeCode", namespaces=namespaces)
        inv_type = 'in_invoice'
        if doc_type_xpath:
            inv_type_code = doc_type_xpath[0].text
            if inv_type_code not in ['INV01', 'INV02']:
                raise UserError(_(
                    "This Finvoice XML file is not an invoice/refund file "
                    "(InvoiceTypeCode is %s") % inv_type_code)
            if inv_type_code == 'INV02':
                inv_type = 'in_refund'
        inv_number_xpath = xml_root.xpath(
            './InvoiceDetails/InvoiceNumber', namespaces=namespaces)
        ord_number_xpath = xml_root.xpath(
            './InvoiceDetails/OriginCode', namespaces=namespaces)
        origin = False
        if ord_number_xpath:
            origin = ord_number_xpath[0].text
        supplier_xpath = xml_root.xpath(
            './SellerPartyDetails',
            namespaces=namespaces)
        supplier_dict = self.finvoice_parse_supplier_party(
            supplier_xpath[0], namespaces)
        customer_xpath = xml_root.xpath(
            './BuyerPartyDetails',
            namespaces=namespaces)
        company_dict_full = self.finvoice_parse_customer_party(
            customer_xpath[0], namespaces)
        company_dict = {}
        # We only take the "official references" for company_dict
        if company_dict_full.get('vat'):
            company_dict['vat'] = company_dict_full['vat']
        if company_dict_full.get('ref'):
            company_dict['ref'] = company_dict_full['ref']
        date_xpath = xml_root.xpath(
            './InvoiceDetails/InvoiceDate', namespaces=namespaces)

        # TODO: support other date formats than CCYYMMDD
        date_format = date_xpath[0].attrib.get('Format')
        if date_format != 'CCYYMMDD':
            msg = _('Invalid invoice date format: %s') % date_format
            raise UserError(msg)

        date_dt = datetime.strptime(date_xpath[0].text, '%Y%m%d')
        date_str = fields.Date.to_string(date_dt)

        date_due_xpath = xml_root.xpath(
            "./InvoiceDetails/PaymentTermsDetails/InvoiceDueDate",
            namespaces=namespaces)
        date_due_str = False
        if date_due_xpath:
            # TODO: support other date formats than CCYYMMDD
            date_format = date_due_xpath[0].attrib.get('Format')
            if date_format != 'CCYYMMDD':
                msg = _('Invalid invoice due date format: %s') % date_format
                raise UserError(msg)

            date_due_dt = datetime.strptime(date_due_xpath[0].text,
                                            '%Y%m%d')
            date_due_str = fields.Date.to_string(date_due_dt)

        total_untaxed_xpath = xml_root.xpath(
            "./InvoiceDetails/InvoiceTotalVatExcludedAmount",
            namespaces=namespaces)
        currency_iso = total_untaxed_xpath[0].attrib.get(
            'AmountCurrencyIdentifier')
        amount_untaxed = _to_float(total_untaxed_xpath[0].text)
        amount_total_xpath = xml_root.xpath(
            "./InvoiceDetails/InvoiceTotalVatIncludedAmount",
            namespaces=namespaces)
        if amount_total_xpath:
            amount_total = _to_float(amount_total_xpath[0].text)

        epi_details = xml_root.xpath("./EpiDetails", namespaces=namespaces)
        iban_xpath = bic_xpath = ref_number = False

        if epi_details:
            iban_xpath = epi_details[0].xpath(
                "./EpiPartyDetails/EpiBeneficiaryPartyDetails"
                "/EpiAccountID[@IdentificationSchemeName='IBAN']",
                namespaces=namespaces)
            bic_xpath = epi_details[0].xpath(
                "./EpiPartyDetails/EpiBfiPartyDetails"
                "/EpiBfiIdentifier[@IdentificationSchemeName='BIC']",
                namespaces=namespaces)
            reference_xpath = epi_details[0].xpath(
                "./EpiPaymentInstructionDetails/EpiRemittanceInfoIdentifier"
            )

            ref_number = reference_xpath and reference_xpath[0].text or False

        attachments = self.get_finvoice_attachments(xml_root, namespaces)
        res_lines = []
        counters = {'lines': 0.0}
        inv_line_xpath = xml_root.xpath(
            "./InvoiceRow", namespaces=namespaces)
        for iline in inv_line_xpath:
            line_vals = self.parse_finvoice_invoice_line(
                iline, counters, namespaces)
            if line_vals is False:
                continue
            res_lines.append(line_vals)

        res = {
            'type': inv_type,
            'partner': supplier_dict,
            'company': company_dict,
            'invoice_number': inv_number_xpath[0].text,
            'origin': origin,
            'date': date_str,
            'date_due': date_due_str,
            'currency': {'iso': currency_iso},
            'amount_total': amount_total,
            'amount_untaxed': amount_untaxed,
            'iban': iban_xpath and iban_xpath[0].text or False,
            'bic': bic_xpath and bic_xpath[0].text or False,
            'lines': res_lines,
            'attachments': attachments,
        }

        if hasattr(self, 'ref_number'):
            res['ref_number'] = ref_number

        if hasattr(self, 'payment_reference'):
            res['payment_reference'] = ref_number

        logger.info('Result of Finvoice XML parsing: %s', res)

        return res
