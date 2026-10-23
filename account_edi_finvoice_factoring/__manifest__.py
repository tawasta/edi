##############################################################################
#
#    Author: Futural Oy
#    Copyright 2026 Futural Oy (https://futural.fi)
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Lesser General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
#    GNU Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public License
#    along with this program. If not, see http://www.gnu.org/licenses/lgpl.html
#
##############################################################################

{
    "name": "Factoring Agreement Details for Finvoice 3.0",
    "summary": "Add factoring agreement details to Finvoice 3.0 EDI",
    "version": "17.0.1.0.0",
    "category": "Accounting",
    "website": "https://github.com/tawasta/edi",
    "author": "Futural",
    "license": "LGPL-3",
    "application": False,
    "installable": True,
    "auto_install": True,
    "external_dependencies": {"python": [], "bin": []},
    "depends": ["account_edi_finvoice", "partner_factoring_contract"],
    "data": [
        "data/finvoice_template.xml",
    ],
    "demo": [],
}
