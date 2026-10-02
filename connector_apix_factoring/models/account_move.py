from odoo import models

class AccountMove(models.Model):
    _inherit = "account.move"

    def _get_apix_to_factoring(self):
        self.ensure_one()
        res = super()._get_apix_to_factoring()

        if self.factoring_contract_id:
            res = True

        return res
        