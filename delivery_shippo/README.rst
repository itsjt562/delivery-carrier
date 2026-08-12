========================
Shippo Shipping OCA
========================

Skeleton module, ported from ``delivery_easypost_oca`` in this same repo.
Not installable yet -- see the build plan doc for what's real vs. stubbed:
``coaching/_planning/delivery_shippo-build-plan-2026-08-11.md`` (private repo,
not in this fork).

Status as of 2026-08-11:

* ``models/shippo_request.py`` -- real REST wrapper, verified against live
  Shippo sandbox (test token) for /shipments/ and /transactions.
* ``models/delivery_carrier.py`` -- single-package rate/buy/track/cancel
  path implemented. Multi-package-per-picking NOT ported from the EasyPost
  module yet.
* ``models/shippo_request.py refund_transaction`` -- Shippo supports a real
  void/refund endpoint. delivery_easypost_oca has no cancel support at all
  (``easypost_oca_cancel_shipment`` just raises UserError). This module does
  better here once wired up.
* Parcel dimensions are hardcoded placeholders in
  ``_shippo_prepare_parcel`` -- do not ship live before wiring real
  ``stock.package.type`` dims.
