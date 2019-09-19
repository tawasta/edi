# -*- coding: utf-8 -*-
import logging
from odoo import models, _, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class BusinessDocumentImport(models.AbstractModel):
    _inherit = 'business.document.import'

    @api.model
    def _match_product(self, product_dict, chatter_msg, seller=False):
        """
        If product is not found, auto-create one
        """
        try:
            # Try to match the product
            return super(BusinessDocumentImport, self)._match_product(
                product_dict=product_dict,
                chatter_msg=chatter_msg,
                seller=seller,
            )
        except UserError:
            # Product can't be matched. Try to create a new one
            _logger.warning(_("Could not find a product"))
            product_product = self.env['product.product']

            print product_dict

            product = product_product.create(product_dict)

            if product:
                return product
            else:
                raise UserError(_("Could not create a product"))
