# © 2016-2017 Akretion (Alexis de Lattre <alexis.delattre@akretion.com>)
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sale Order UBL",
    "version": "17.0.1.0.5",
    "category": "Sale Management",
    "license": "AGPL-3",
    "summary": "Embed UBL XML file inside the PDF sale order",
    "author": "Akretion,Odoo Community Association (OCA), Futural",
    "website": "https://github.com/tawasta/edi",
    "depends": [
        # Odoo/core
        "sale",
        # OCA/edi
        "base_ubl_generate",
        # OCA/community-data-files
        "account_tax_unece",
        # OCA/reporting-engine
        "pdf_xml_attachment",
    ],
    "installable": True,
    "data": [
        "report/ir_actions_report.xml",
    ],
}
