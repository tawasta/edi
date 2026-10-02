import textwrap

from odoo import models

# Finvoice 3.0 schema: each FactoringFreeText element is at most 70
# characters, but the element can be repeated without limit.
FACTORING_FREE_TEXT_MAX_LENGTH = 70


class FactoringContract(models.Model):
    _inherit = "factoring.contract"

    def _get_finvoice_free_texts(self):
        """Split the free text into Finvoice FactoringFreeText chunks."""
        self.ensure_one()
        return textwrap.wrap(self.free_text or "", FACTORING_FREE_TEXT_MAX_LENGTH)
