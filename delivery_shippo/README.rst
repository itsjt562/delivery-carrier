========================
Shippo Shipping OCA
========================

Ported from ``delivery_easypost_oca`` in this same repo. See the build plan
doc for full history: ``coaching/_planning/delivery_shippo-build-plan-2026-08-11.md``
(private repo, not in this fork).

Status as of 2026-08-12:

* ``models/shippo_request.py`` -- real REST wrapper, verified against live
  Shippo sandbox (test token) for /shipments/ and /transactions.
* ``models/delivery_carrier.py`` -- single-package rate/buy/track/cancel
  path implemented. Multi-package-per-picking NOT ported from the EasyPost
  module yet -- not needed, Helm ships one mailer per order.
* ``models/shippo_request.py refund_transaction`` -- Shippo supports a real
  void/refund endpoint. delivery_easypost_oca has no cancel support at all
  (``easypost_oca_cancel_shipment`` just raises UserError). This module does
  better here once wired up.
* Parcel dims are real: 8x12x1in bubble mailer, 8oz packaging weight,
  confirmed with Joseph 2026-08-12. Hardcoded as class constants on
  ``DeliveryCarrier`` rather than a ``stock.package.type`` record, since
  there's exactly one packaging format in use -- revisit if that changes.
* Test suite passing: 4 tests, 0 failures, run against a disposable clone
  of production (not against odoo-cloudzy itself).
