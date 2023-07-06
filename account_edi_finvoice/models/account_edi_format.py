import logging
import re
import textwrap
from datetime import datetime

from lxml import etree

from odoo import _, api, models, tools
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import Form
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
        return tree.tag == "Finvoice"

    def _get_finvoice_values(self, invoice):
        def format_monetary(amount):
            amount = float_repr(amount, invoice.currency_id.decimal_places)
            amount = str(amount).replace(".", ",")

            return amount

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
            overdue_fine_percent = False

        if hasattr(invoice, "agreement_identifier"):
            # Allows implementing agreement identifier
            agreement_identifier = invoice.agreement_identifier
        else:
            agreement_identifier = invoice.ref or invoice.name

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

        xml_string = self.env["ir.qweb"]._render(
            "account_edi_finvoice.export_finvoice", self._get_finvoice_values(invoice)
        )

        # Validate the content. This will NOT raise an error for user
        self._finvoice_check_xml_schema(xml_string)

        # Add file encoding (schema validation doesn't want this)
        xml_string = b"<?xml version='1.0' encoding='UTF-8'?>" + xml_string

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

    def _finvoice_get_xml_schema(self, version="3.0"):
        xsd_file = f"account_edi_finvoice/static/schema/Finvoice{version}.xsd"
        xsd_etree_obj = etree.parse(tools.file_open(xsd_file))
        finvoice_schema = etree.XMLSchema(xsd_etree_obj)

        return finvoice_schema

    @api.model
    def _finvoice_check_xml_schema(self, xml, version="3.0"):
        """Validate the XML file against the XSD"""
        finvoice_schema = self._finvoice_get_xml_schema(version)

        if isinstance(xml, str):
            t = etree.ElementTree(etree.fromstring(xml))
        elif isinstance(xml, bytes):
            t = etree.ElementTree(etree.fromstring(xml.decode("UTF-8")))
        else:
            t = xml

        try:
            finvoice_schema.assertValid(t)
        except etree.DocumentInvalid as e:
            # if the validation of the XSD fails, we arrive here
            _logger.warning("The XML file is invalid against the XML Schema Definition")
            _logger.warning(xml)
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

    def _create_invoice_from_xml_tree(self, filename, tree, journal=None):
        self.ensure_one()
        if self._is_finvoice(filename, tree):
            return self._import_finvoice(tree, self.env["account.move"])
        return super()._create_invoice_from_xml_tree(filename, tree)

    def _update_invoice_from_xml_tree(self, filename, tree, invoice):
        self.ensure_one()
        if self._is_finvoice(filename, tree):
            return self._import_finvoice(tree, invoice)
        return super()._update_invoice_from_xml_tree(filename, tree, invoice)

    def _find_attribute(self, xpath, element, attribute):
        element = element.xpath(xpath, namespaces=element.nsmap)
        return element[0].attrib.get(attribute) if element else None

    def _find_values_joined(self, xpath, element, join_character="\n"):
        """
        Get a joined string from multiple values
        """
        elements = element.xpath(xpath, namespaces=element.nsmap)
        return join_character.join(x.text for x in elements)

    # flake8: noqa: C901
    def _import_finvoice(self, tree, invoice):
        """
        Import finvoice document as Odoo invoice
        """

        def _find_value(xpath, element=tree):
            return self._find_value(xpath, element, element.nsmap)

        ns = tree.nsmap

        # Check XML schema to avoid headaches trying to import invalid files
        self._finvoice_check_xml_schema(tree)

        invoice_type = self._get_invoice_type(
            _find_value("./InvoiceDetails/InvoiceTypeCode")
        )
        journal_id = invoice.with_context(
            {"default_move_type": invoice_type}
        )._get_default_journal()

        invoice = invoice.with_context(
            default_move_type=invoice_type, default_journal_id=journal_id.id
        )

        with Form(invoice) as invoice_form:
            self_ctx = self.with_company(self.env.company.id)

            # region SellerPartyDetails
            spd = "SellerPartyDetails"

            business_code = _find_value(f"./{spd}/SellerPartyIdentifier")
            vat = _find_value(f"./{spd}/SellerOrganisationTaxCode")

            # Hacks for insufficient/defective Finvoice XML
            business_code_regex = "^[0-9]{7}[-][0-9]$"

            # Can't find a VAT, use business id instead
            if (
                not vat
                and business_code
                and re.search(business_code_regex, business_code)
            ):
                # TODO: this is pretty unreliable
                vat = "FI%s" % re.sub("[^0-9]", "", business_code)
            elif vat and re.search(business_code_regex, vat):
                # Business Code is incorrectly given in VAT field (this happens)
                vat = "FI%s" % re.sub("[^0-9]", "", vat)

            spad = "SellerPostalAddressDetails"
            partner_vals = {
                "business_code": business_code,
                "vat": vat,
                "name": _find_value(f"./{spd}/SellerOrganisationName"),
                "phone": _find_value(f"./{spd}/SellerPhoneNumberIdentifier"),
                "email": _find_value(f"./{spd}/SellerEmailaddressIdentifier"),
                "street": _find_value(f"./{spd}/{spad}/SellerStreetName"),
                "city": _find_value(f"./{spd}/{spad}/SellerTownName"),
                "zip": _find_value(f"./{spd}/{spad}/SellerPostCodeIdentifier"),
                "company_type": "company",
            }

            partner_id = self_ctx._retrieve_partner(
                name=partner_vals.get("name"),
                phone=partner_vals.get("phone"),
                mail=partner_vals.get("email"),
                vat=vat,
            )

            if not partner_id:
                _logger.info(
                    _(
                        f"Partner not found. Creating a new partner with values: {partner_vals}"
                    )
                )
                partner_id = self.env["res.partner"].create(partner_vals)

            invoice_form.partner_id = partner_id
            # endregion

            # region InvoiceDetails
            ind = "InvoiceDetails"
            invoice_form.ref = _find_value(
                f"./{ind}/SellerReferenceIdentifier"
            ) or _find_value(f"./{ind}/InvoiceNumber")

            invoice_date = _find_value(f"./{ind}/InvoiceDate")
            invoice_form.invoice_date = datetime.strptime(invoice_date, "%Y%m%d")
            if hasattr(invoice, "agreement_identifier"):
                invoice_form.agreement_identifier = _find_value(
                    f"./{ind}/AgreementIdentifier"
                )

            invoice_form.narration = self._find_values_joined(
                f"./{ind}/InvoiceFreeText",
                tree,
            )

            ptd = "PaymentTermsDetails"
            invoice_form.narration += self._find_values_joined(
                f"./{ind}/{ptd}/PaymentTermsFreeText", tree
            )

            invoice_date_due = _find_value(f"./{ind}/{ptd}/InvoiceDueDate")
            invoice_form.invoice_date = datetime.strptime(invoice_date_due, "%Y%m%d")

            # endregion

            # region InvoiceRows
            lines = tree.xpath("./InvoiceRow", namespaces=ns)
            for line in lines:
                with invoice_form.invoice_line_ids.new() as line_form:
                    # Try to find a product by default code, name or barcode
                    if _find_value("./BuyerArticleIdentifier", line):
                        default_code = _find_value("./BuyerArticleIdentifier", line)
                    else:
                        default_code = _find_value("./ArticleIdentifier", line)
                    article_name = _find_value("./ArticleName", line)
                    article_description = _find_value("./ArticleDescription", line)
                    ean_code = _find_value("./EanCode", line)

                    product_id = self_ctx._retrieve_product(
                        default_code=default_code,
                        name=article_name,
                        barcode=ean_code,
                    )
                    # TODO: An option to auto-create products

                    line_form.product_id = product_id

                    if product_id:
                        accounts = product_id.product_tmpl_id._get_product_accounts()

                        if invoice_type == "in_invoice":
                            line_form.account_id = accounts["expense"]
                        elif invoice_type == "out_invoice":
                            line_form.account_id = accounts["income"]
                    else:
                        line_form.account_id = journal_id.default_account_id

                    if not article_name and not default_code:
                        # Comment line
                        # TODO: comment lines not working yet
                        line_form.display_type = "line_note"
                        line_form.account_id = self.env["account.account"]

                    quantity = (
                        self._to_float(_find_value("./InvoicedQuantity", line)) or 1
                    )
                    line_form.quantity = quantity

                    unit_code = self._find_attribute(
                        "./InvoicedQuantity", line, "QuantityUnitCode"
                    )
                    if product_id:
                        uom = self.env["uom.uom"].search(
                            [("name", "ilike", unit_code)], limit=1
                        )
                        # TODO: an option to auto-create a missing UOM
                        if not uom:
                            uom = self.env.ref("uom.product_uom_unit")

                        line_form.product_uom_id = uom

                    # Construct a unit price

                    # Try to find UnitPriceAmount
                    price_unit = _find_value("./UnitPriceAmount", line)

                    if not price_unit:
                        # Didn't find UnitPriceAmount. Try RowVatExcludedAmount
                        price_subtotal = _find_value("./RowVatExcludedAmount", line)
                        if price_subtotal:
                            price_unit = self._to_float(price_subtotal) / quantity

                    line_form.price_unit = self._to_float(price_unit)

                    line_form.discount = self._to_float(
                        _find_value("./RowDiscountPercent", line)
                    )

                    # Construct a line name, if product is not found
                    line_name = ""
                    if not product_id:
                        if article_name:
                            line_name += f"{article_name}"
                        if article_description:
                            line_name += f"{article_description}"

                    line_name += self._find_values_joined("./RowFreeText", line)
                    line_form.name = line_name

                    # Taxes
                    # We are not using _retrieve_tax()
                    # as it might return a tax with prices included
                    line_form.tax_ids.clear()
                    tax_amount = self._to_float(
                        _find_value("./RowVatRatePercent", line)
                    )
                    if tax_amount:
                        tax_domain = [
                            ("amount", "=", tax_amount),
                            ("type_tax_use", "=", invoice_form.journal_id.type),
                            # The subtotal will be saved as untaxed amount
                            ("price_include", "=", False),
                            ("company_id", "=", self.env.company.id),
                        ]

                        tax = self.env["account.tax"].search(
                            tax_domain, order="sequence ASC", limit=1
                        )

                        if not tax:
                            raise ValidationError(
                                _(f"Could not find a tax for {tax_amount}")
                            )

                        line_form.tax_ids.add(tax)

                # TODO: handle SubInvoiceRows

            # endregion

            # region EpiDetails
            ede = "EpiDetails"
            payment_reference = invoice_form.payment_reference = _find_value(
                f"./{ede}/EpiIdentificationDetails/EpiReference"
            )

            if not payment_reference:
                # Try to get payment reference from SellersBuyerIdentifier
                # It's not officially for a payment reference,
                # but is sometimes incorrectly used as it was
                payment_reference = invoice_form.payment_reference = _find_value(
                    f"./{ind}/SellersBuyerIdentifier"
                )

            invoice_form.payment_reference = payment_reference

            epd = "EpiPartyDetails"

            partner_bank_id = self_ctx._retrieve_bank_account(
                _find_value(f"./{ede}/{epd}/EpiBeneficiaryPartyDetails/EpiAccountID"),
                partner_id=partner_id.id,
                bic=_find_value(f"./{ede}/{epd}/EpiBfiPartyDetails/EpiBfiIdentifier"),
            )

            if partner_bank_id:
                invoice_form.partner_bank_id = partner_bank_id
            # endregion

            invoice = invoice_form.save()

            return invoice

    def _get_invoice_type(self, inv_type_code):
        """
        Get invoice type from Finvoice type code
        """

        if inv_type_code in ["INV01", "INV03", "INV04", "INV05", "INV08"]:
            # INV01 Invoice (Lasku)
            # INV03 Interest Invoice (Korkolasku)
            # INV04 Internal Invoice (Sisäinen lasku)
            # INV05 Collection Bill (Perintälasku)
            # INV08 Notification Invoice (Huomatuslasku)

            inv_type = "in_invoice"
        elif inv_type_code == "INV02":
            # INV02 Refund (Hyvityslasku)
            inv_type = "in_refund"
        else:
            raise UserError(_("This Finvoice XML file is not an invoice/refund file"))

        return inv_type

    def _to_float(self, string_number):
        # Format a '1 234,56' string as float 1234.56

        float_number = 0

        if isinstance(string_number, float):
            float_number = string_number
        elif isinstance(string_number, int):
            float_number = float(string_number)
        elif isinstance(string_number, str):
            if "." in string_number and "," in string_number:
                # TODO: Add support for comma as thousands separator (1,000.00)
                msg = _(
                    f"Using comma as thousands separator not supported! ({string_number})"
                )
                raise UserError(msg)

            # Replace comma with period
            string_number = string_number.replace(",", ".")

            # Replace non-numeric
            string_number = re.sub(r"[^\d.-]", "", string_number)

            float_number = float(string_number)

        return float_number

    def _retrieve_bank_account(self, account_number, partner_id, bic=False):
        """
        Search for bank account or create a new one
        """
        if not account_number:
            return None

        account_numbers = [account_number, account_number.replace(" ", "")]
        partner_bank = self.env["res.partner.bank"]

        domain = [
            ("acc_number", "in", account_numbers),
            ("partner_id", "=", partner_id),
            ("company_id", "=", self.env.company.id),
        ]
        bank_account = partner_bank.search(domain, limit=1)

        if not bank_account:
            account_vals = {
                "acc_number": account_number,
                "partner_id": partner_id,
                "company_id": self.env.company.id,
                # Automatic journal selection doesn't work
                "journal_id": False,
            }
            if bic:
                bank_id = self.env["res.bank"].search(
                    [("bic", "=", bic.upper())], limit=1
                )
                if bank_id:
                    account_vals["bank_id"] = bank_id.id

            bank_account = partner_bank.create(account_vals)

        return bank_account
