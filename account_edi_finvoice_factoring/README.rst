.. image:: https://img.shields.io/badge/licence-LGPL--3-blue.svg
   :target: http://www.gnu.org/licenses/lgpl-3.0-standalone.html
   :alt: License: LGPL-3

============================================
Factoring Agreement Details for Finvoice 3.0
============================================

Adds the Finvoice 3.0 ``FactoringAgreementDetails`` block (agreement
identifier and factoring type code) to exported Finvoice XML files,
based on the invoice's Factoring Contract
(``partner_factoring_contract``).

Features
========

* When an invoice has a Factoring Contract set, the exported Finvoice
  XML includes a ``FactoringAgreementDetails`` block with the
  contract's agreement identifier and, if set, its type code.
* Invoices without a Factoring Contract are unaffected - no block is
  added.

Configuration
=============
\-

Usage
=====

* Set a Factoring Contract on the invoice or its customer (see the
  ``partner_factoring_contract`` module), then export the invoice as
  Finvoice as usual.

Known issues / Roadmap
======================
\-

Credits
=======

Contributors
------------

* Valtteri Lattu <valtteri.lattu@futural.fi>

Maintainer
----------

.. image:: https://futural.fi/templates/tawastrap/images/logo.png
   :alt: Futural Oy
   :target: https://futural.fi/

This module is maintained by Futural Oy
