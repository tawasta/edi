import logging
import re
from datetime import datetime

from odoo import _, api, models, tools
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_edi_decoder(self, file_data, new=False):
        if file_data["type"] == "xml":
            if self._is_finvoice(file_data["xml_tree"]):
                self._import_finvoice(file_data["xml_tree"], self)

        return super()._get_edi_decoder(file_data, new=new)

    def _is_finvoice(self, tree):
        return tree.tag == "Finvoice"

    # flake8: noqa: C901
    def _import_finvoice(self, tree, invoice, company_id=False):
        """
        Import finvoice document as Odoo invoice
        """

        edi_format = self.env["account.edi.format"]
        edi_common = self.env["account.edi.common"]

        def _find_value(xpath, element=tree):
            return edi_common._find_value(xpath, element, element.nsmap)

        ns = tree.nsmap

        # Check XML schema to avoid headaches trying to import invalid files
        edi_format._finvoice_check_xml_schema(tree)

        invoice_type = edi_format._get_invoice_type(
            _find_value("./InvoiceDetails/InvoiceTypeCode")
        )
        if not company_id:
            company_id = self.env.company.id
        invoice = invoice.with_company(company_id).with_context(
            default_move_type=invoice_type
        )

        # region SellerPartyDetails
        spd = "SellerPartyDetails"

        business_code = _find_value(f"./{spd}/SellerPartyIdentifier")
        vat = _find_value(f"./{spd}/SellerOrganisationTaxCode")

        # Hacks for insufficient/defective Finvoice XML
        business_code_regex = "^[0-9]{7}[-][0-9]$"

        # Can't find a VAT, use business id instead
        if not vat and business_code and re.search(business_code_regex, business_code):
            # TODO: this is pretty unreliable
            vat = "FI%s" % re.sub("[^0-9]", "", business_code)
        elif vat and re.search(business_code_regex, vat):
            # Business Code is incorrectly given in VAT field (this happens)
            vat = "FI%s" % re.sub("[^0-9]", "", vat)

        spad = "SellerPostalAddressDetails"

        edi_common._import_retrieve_and_fill_partner(
            invoice,
            name=_find_value(f"./{spd}/SellerOrganisationName"),
            phone=_find_value(f"./{spd}/SellerPhoneNumberIdentifier"),
            mail=_find_value(f"./{spd}/SellerEmailaddressIdentifier"),
            vat=vat,
        )

        partner_vals = {
            "company_registry": business_code,
            "street": _find_value(f"./{spd}/{spad}/SellerStreetName"),
            "city": _find_value(f"./{spd}/{spad}/SellerTownName"),
            "zip": _find_value(f"./{spd}/{spad}/SellerPostCodeIdentifier"),
        }

        invoice.partner_id.write(partner_vals)
        # endregion

        # region InvoiceDetails
        ind = "InvoiceDetails"
        invoice.ref = _find_value(f"./{ind}/SellerReferenceIdentifier") or _find_value(
            f"./{ind}/InvoiceNumber"
        )

        invoice_date = _find_value(f"./{ind}/InvoiceDate")
        invoice.invoice_date = datetime.strptime(invoice_date, "%Y%m%d")
        if hasattr(invoice, "agreement_identifier"):
            invoice.agreement_identifier = _find_value(f"./{ind}/AgreementIdentifier")

        invoice.narration = edi_format._find_values_joined(
            f"./{ind}/InvoiceFreeText",
            tree,
        )

        ptd = "PaymentTermsDetails"
        invoice.narration += edi_format._find_values_joined(
            f"./{ind}/{ptd}/PaymentTermsFreeText", tree
        )

        invoice_date_due = _find_value(f"./{ind}/{ptd}/InvoiceDueDate")
        invoice.invoice_date_due = datetime.strptime(invoice_date_due, "%Y%m%d")

        # endregion

        # region InvoiceRows
        lines = tree.xpath("./InvoiceRow", namespaces=ns)
        line_number = 0
        line_count = len(lines)

        for line in lines:
            line_number += 1
            _logger.debug("Importing line {}/{}".format(line_number, line_count))
            line_values = {"move_id": invoice.id}

            if _find_value("./BuyerArticleIdentifier", line):
                default_code = _find_value("./BuyerArticleIdentifier", line)
            else:
                default_code = _find_value("./ArticleIdentifier", line)
            article_name = _find_value("./ArticleName", line)
            article_description = _find_value("./ArticleDescription", line)
            ean_code = _find_value("./EanCode", line)

            # Construct a unit price
            quantity = (
                edi_format._to_float(_find_value("./InvoicedQuantity", line)) or 1
            )
            # Try to find UnitPriceAmount
            price_unit = _find_value("./UnitPriceAmount", line)

            if not price_unit or edi_format._to_float(price_unit) == 0:
                # Didn't find UnitPriceAmount. Try RowVatExcludedAmount
                price_subtotal = _find_value("./RowVatExcludedAmount", line)
                price_subtotal = edi_format._to_float(price_subtotal)
                if price_subtotal:
                    price_unit = price_subtotal / quantity

            if not price_unit:
                price_unit = 0

            if article_name:
                _logger.debug("Importing '{}'".format(article_name))

            if line_count > 200 and not price_unit:
                # If invoice has more than 200 lines, skip zero lines to prevent a timeout
                # This can be disabled (or limit raised) after line import is optimized
                _logger.debug("Skipping a zero line due to a long invoice")
                continue

            # Try to find a product by default code, name or barcode
            product_id = self.env["product.product"]._retrieve_product(
                default_code=default_code,
                name=article_name,
                barcode=ean_code,
            )
            # TODO: An option to auto-create products

            line_values["product_id"] = product_id

            if product_id:
                accounts = product_id.product_tmpl_id._get_product_accounts()

                if invoice_type == "in_invoice":
                    line_values["account_id"] = accounts["expense"]
                elif invoice_type == "out_invoice":
                    line_values["account_id"] = accounts["income"]

            # Construct a line name, if product is not found
            line_name = ""
            if not product_id:
                if article_name:
                    line_name += f"{article_name}"
                if article_description:
                    line_name += f"\n{article_description}"

            line_name += "\n" + edi_format._find_values_joined("./RowFreeText", line)
            line_values["name"] = line_name

            if not article_name and not default_code:
                # Comment line
                # TODO: comment lines not working yet
                line_values["display_type"] = "line_note"
                line_values["account_id"] = self.env["account.account"]

            line_values["quantity"] = quantity

            unit_code = edi_format._find_attribute(
                "./InvoicedQuantity", line, "QuantityUnitCode"
            )
            if product_id:
                uom = self.env["uom.uom"].search(
                    [("name", "ilike", unit_code)], limit=1
                )
                # TODO: an option to auto-create a missing UOM
                if not uom:
                    uom = self.env.ref("uom.product_uom_unit")

                line_values["product_uom_id"] = uom

            line_values["price_unit"] = edi_format._to_float(price_unit)

            line_values["discount"] = edi_format._to_float(
                _find_value("./RowDiscountPercent", line)
            )

            # Taxes
            # We are not using _retrieve_tax()
            # as it might return a tax with prices included
            tax_amount = edi_format._to_float(_find_value("./RowVatRatePercent", line))
            if tax_amount:
                tax_domain = [
                    ("amount", "=", tax_amount),
                    ("type_tax_use", "=", invoice.journal_id.type),
                    # The subtotal will be saved as untaxed amount
                    ("price_include", "=", False),
                    ("company_id", "=", company_id),
                ]

                tax = self.env["account.tax"].search(
                    tax_domain, order="sequence ASC", limit=1
                )

                if not tax:
                    raise ValidationError(_(f"Could not find a tax for {tax_amount}"))

                line_values["tax_ids"] = tax

            print(line_values)
            invoice.invoice_line_ids.create(line_values)

            # TODO: handle SubInvoiceRows

        # endregion

        # region EpiDetails
        ede = "EpiDetails"
        payment_reference = invoice.payment_reference = _find_value(
            f"./{ede}/EpiIdentificationDetails/EpiReference"
        )

        if not payment_reference:
            # Try to get payment reference from SellersBuyerIdentifier
            # It's not officially for a payment reference,
            # but is sometimes incorrectly used as it was
            payment_reference = invoice.payment_reference = _find_value(
                f"./{ind}/SellersBuyerIdentifier"
            )

        invoice.payment_reference = payment_reference

        epd = "EpiPartyDetails"

        partner_bank_id = edi_format._retrieve_bank_account(
            _find_value(f"./{ede}/{epd}/EpiBeneficiaryPartyDetails/EpiAccountID"),
            partner_id=invoice.partner_id.id,
            bic=_find_value(f"./{ede}/{epd}/EpiBfiPartyDetails/EpiBfiIdentifier"),
            company_id=company_id,
        )

        if partner_bank_id:
            invoice.partner_bank_id = partner_bank_id
        # endregion

        return invoice
