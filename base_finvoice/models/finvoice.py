# -*- coding: utf-8 -*-
from odoo import models, api, tools, _
from StringIO import StringIO
from lxml import etree
from odoo.exceptions import UserError
import re

import logging
logger = logging.getLogger(__name__)


class BaseFinvoice(models.AbstractModel):
    _name = 'base.finvoice'
    _description = 'Common methods to generate and parse FINVOICE XML files'

    def finvoice_parse_product(self, line_node, ns):
        code_xpath = line_node.xpath(
            "./ArticleIdentifier", namespaces=ns)

        product_dict = {
            'code': code_xpath and code_xpath[0].text or False,
            }

        return product_dict

    @api.model
    def finvoice_parse_customer_party(self, party_node, ns):
        partner_dict = \
            self.finvoice_parse_party(party_node, 'Buyer', ns)
        return partner_dict

    @api.model
    def finvoice_parse_supplier_party(self, party_node, ns):
        partner_dict = \
            self.finvoice_parse_party(party_node, 'Seller', ns)
        return partner_dict

    @api.model
    def finvoice_parse_delivery_party(self, party_node, ns):
        partner_dict = \
            self.finvoice_parse_party(party_node, 'Delivery', ns)
        return partner_dict

    @api.model
    def finvoice_parse_party(self, party_node, party_type, ns):
        partner_ref_xpath = party_node.xpath(
            './%sPartyIdentifier' % party_type, namespaces=ns)
        partner_name_xpath = party_node.xpath(
            './%sOrganisationName' % party_type, namespaces=ns)
        vat_xpath = party_node.xpath(
            './%sOrganisationTaxCode' % party_type, namespaces=ns)
        email_xpath = party_node.xpath(
            './%sEmailaddressIdentifier' % party_type, namespaces=ns)
        phone_xpath = party_node.xpath(
            './%sPhoneNumberIdentifier' % party_type, namespaces=ns)
        fax_xpath = party_node.xpath(
            './%sFaxNumberIdentifier' % party_type, namespaces=ns)
        website_xpath = party_node.xpath(
            './%sPartyIdentifierUrlText' % party_type, namespaces=ns)

        vat = vat_xpath and vat_xpath[0].text or False
        ref = partner_ref_xpath and partner_ref_xpath[0].text or False

        # Can't find a VAT, use business id instead
        if not vat and ref:
            # TODO: this is pretty unreliable
            vat = 'FI%s' % re.sub('[^0-9]', '', ref)

        partner_dict = {
            'business_id': ref,
            'ref': ref,
            'vat': vat,
            'name': partner_name_xpath and partner_name_xpath[0].text or False,
            'email': email_xpath and email_xpath[0].text or False,
            'website': website_xpath and website_xpath[0].text or False,
            'phone': phone_xpath and phone_xpath[0].text or False,
            'fax': fax_xpath and fax_xpath[0].text or False,
        }
        address_xpath = party_node.xpath(
            './%sPostalAddressDetails' % party_type, namespaces=ns)

        if address_xpath:
            address_dict = self.finvoice_parse_address(address_xpath[0], ns)
            partner_dict.update(address_dict)

        return partner_dict

    @api.model
    def finvoice_parse_address(self, address_node, party_type, ns):
        country_code_xpath = address_node.xpath('./CountryCode', namespaces=ns)
        country_code = country_code_xpath and country_code_xpath[0].text \
                       or False
        zip_xpath = address_node.xpath(
            './%sPostCodeIdentifier' % party_type, namespaces=ns)
        zip = zip_xpath and zip_xpath[0].text and \
              zip_xpath[0].text.replace(' ', '') or False

        street_xpath = address_node.xpath(
            './%sStreetName' % party_type, namespaces=ns)
        street = street_xpath and street_xpath[0].text or False

        city_xpath = address_node.xpath(
            './%sTownName' % party_type, namespaces=ns)
        city = city_xpath and street_xpath[0].text or False

        address_dict = {
            'street': street,
            'city': city,
            'zip': zip,
            'country_code': country_code,
        }
        return address_dict

    @api.model
    def _finvoice_check_xml_schema(self, xml_string, version='3.0'):
        '''Validate the XML file against the XSD'''
        xsd_file = 'base_finvoice/data/xsd-%s/Finvoice%s.xsd' % (
            version, version)
        xsd_etree_obj = etree.parse(tools.file_open(xsd_file))
        official_schema = etree.XMLSchema(xsd_etree_obj)
        try:
            t = etree.parse(StringIO(xml_string))
            official_schema.assertValid(t)
        except Exception, e:
            # if the validation of the XSD fails, we arrive here
            logger = logging.getLogger(__name__)
            logger.warning(
                "The XML file is invalid against the XML Schema Definition")
            logger.warning(xml_string)
            logger.warning(e)
            raise UserError(_(
                "The Finvoice XML file is not valid against the official "
                "XML Schema Definition. The XML file and the "
                "full error have been written in the server logs. "
                "Here is the error, which may give you an idea on the "
                "cause of the problem : %s.")
                % unicode(e))
        return True
