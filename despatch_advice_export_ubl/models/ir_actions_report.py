from odoo import models


class IrActionsReport(models.Model):
    _inherit = "ir.actions.report"

    def _is_stock_picking_report_ubl(self, report_ref):
        return self._get_report(report_ref).report_name in (
            "despatch_advice_export_ubl.report_despatch_advice_ubl"
        )

    def _render_qweb_pdf_prepare_streams(self, report_ref, data, res_ids=None):
        collected_streams = super()._render_qweb_pdf_prepare_streams(
            report_ref, data, res_ids
        )
        if collected_streams and res_ids and self._is_stock_picking_report_ubl(report_ref):
            report_sudo = self._get_report(report_ref)
            if not self.env.context.get("no_embedded_ubl_xml"):
                records = self.env[report_sudo.model].browse(res_ids)
                for record in records:
                    collected_streams[record.id][
                        "stream"
                    ] = record.add_xml_in_pdf_buffer(
                        collected_streams[record.id]["stream"]
                    )
        return collected_streams
