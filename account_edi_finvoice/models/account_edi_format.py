import textwrap
import logging
from lxml import etree
from datetime import datetime

from odoo import api
from odoo import models
from odoo import _
from odoo import tools
from odoo.tools import float_repr

_logger = logging.getLogger(__name__)

INVOICE_TYPES = {
    "REQ01": "TARJOUSPYYNTÖ",
    "QUO01": "TARJOUS",
    "ORD01": "TILAUS",
    "ORC01": "TILAUSVAHVISTUS",
    "DEV01": "TOIMITUSILMOITUS",
    "INV01": "LASKU",
    "INV02": "HYVITYSLASKU",
    "INV03": "KORKOLASKU",
    "INV04": "SISÄINEN LASKU",
    "INV05": "PERINTÄLASKU",
    "INV06": "PROFORMALASKU",
    "INV07": "ITSELASKUTUS",
    "INV08": "HUOMAUTUSLASKU",
    "INV09": "SUORAMAKSU",
    "TES01": "TESTILASKU",
    "PRI01": "HINNASTO",
    "INF01": "TIEDOTE",
    "DEN01": "TOIMITUSVIRHEILMOITUS",
    "SEI01-09": "TURVALASKU",
    "REC01-09": "KUITTI",
    "RES01-09": "TURVAKUITTI",
    "SDD01": "Suoraveloituksen ennakkoilmoitus",
}


class AccountEdiFormat(models.Model):
    _inherit = "account.edi.format"

    def _is_finvoice(self, filename, tree):
        # TODO
        return False

    def _get_finvoice_values(self, invoice):
        def format_monetary(amount):
            return float_repr(amount, invoice.currency_id.decimal_places)

        def format_date(dt=False):
            # Returns unhyphenated ISO-8601 date
            # CCYY-MM-DD becomes CCYYMMDD
            # 2020-01-02 becomes 20200102

            dt = dt or datetime.now()

            date_format = "%Y%m%d"

            return dt.strftime(date_format)

        # Normal sales invoice
        type_code = "INV01"
        origin_code = "Original"

        if invoice.move_type == "out_refund":
            # Refund invoice
            type_code = "INV02"
            origin_code = "Cancel"

        # Each instance of InvoiceFreeText can be 512 characters
        if invoice.narration:
            free_texts = textwrap.wrap(invoice.narration, 512)
        else:
            free_texts = []

        if hasattr(invoice, "overdue_interest"):
            # Allows implementing overdue fine percent
            overdue_fine_percent = invoice.overdue_interest
        else:
            overdue_fine_percent = ""

        if hasattr(invoice, "agreement_identifier"):
            # Allows implementing agreement identifier
            agreement_identifier = invoice.agreement_identifier
        else:
            agreement_identifier = ""

        return {
            "record": invoice,
            "format_monetary": format_monetary,
            "format_date": format_date,
            "message_timestamp": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "type_code": type_code,
            "type_text": INVOICE_TYPES.get(type_code, ""),
            "origin_code": origin_code,
            "free_texts": free_texts,
            "overdue_fine_percent": overdue_fine_percent,
            "agreement_identifier": agreement_identifier,
        }

    def _export_finvoice(self, invoice):
        self.ensure_one()

        xml_string = b"<?xml version='1.0' encoding='UTF-8'?>"
        xml_string += self.env["ir.qweb"]._render(
            "account_edi_finvoice.export_finvoice", self._get_finvoice_values(invoice)
        )

        # Validate the content. This will NOT raise an error for user
        self._finvoice_check_xml_schema(xml_string)

        xml_name = "%s_finvoice_3_0.xml" % (invoice.name.replace("/", "_"))
        return self.env["ir.attachment"].create(
            {
                "name": xml_name,
                "raw": xml_string,
                "res_model": "account.move",
                "res_id": invoice.id,
                "mimetype": "application/xml",
            }
        )

    def _is_compatible_with_journal(self, journal):
        self.ensure_one()
        res = super()._is_compatible_with_journal(journal)
        if self.code != "finvoice_3_0":
            return res
        return journal.type == "sale"

    def _post_invoice_edi(self, invoices, test_mode=False):
        self.ensure_one()
        if self.code != "finvoice_3_0":
            return super()._post_invoice_edi(
                invoices,
            )
        res = {}
        for invoice in invoices:
            attachment = self._export_finvoice(invoice)
            res[invoice] = {"success": True, "attachment": attachment}
        return res

    @api.model
    def _finvoice_check_xml_schema(self, xml_string, version="3.0"):
        """Validate the XML file against the XSD"""
        xsd_file = f"account_edi_finvoice/static/schema/Finvoice{version}.xsd"
        xsd_etree_obj = etree.parse(tools.file_open(xsd_file))
        finvoice_schema = etree.XMLSchema(xsd_etree_obj)
        try:
            t = etree.ElementTree(etree.fromstring(xml_string))
            finvoice_schema.assertValid(t)
        except Exception as e:
            # if the validation of the XSD fails, we arrive here

            _logger.warning("The XML file is invalid against the XML Schema Definition")
            _logger.warning(xml_string)
            _logger.warning(e)

            msg = _(
                "The Finvoice XML file is not valid against the official "
                "XML Schema Definition. The XML file and the "
                "full error have been written in the server logs. "
                "Here is the error, which may give you an idea on the "
                "cause of the problem : {}.".format(e)
            )

            _logger.error(msg)
        return True
