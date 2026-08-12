from odoo import _, fields, models
from odoo.exceptions import UserError

from .shippo_request import ShippoRequest


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    delivery_type = fields.Selection(
        selection_add=[("shippo", "Shippo")],
        ondelete={
            "shippo": lambda recs: recs.write({"delivery_type": "fixed", "fixed_price": 0})
        },
    )

    shippo_test_api_key = fields.Char(
        "Shippo Test API Key",
        groups="base.group_system",
        help="shippo_test_... token from the Shippo dashboard.",
    )
    shippo_production_api_key = fields.Char(
        "Shippo Production API Key",
        groups="base.group_system",
        help="shippo_live_... token from the Shippo dashboard.",
    )
    shippo_label_file_type = fields.Selection(
        [("PDF", "PDF"), ("PNG", "PNG"), ("ZPLII", "ZPLII")],
        string="Label Format",
        default="PDF",
    )

    # -- rate_shipment ------------------------------------------------------
    def shippo_rate_shipment(self, order):
        sr = ShippoRequest(self)
        shipper = self._shippo_prepare_address(order.warehouse_id.partner_id)
        recipient = self._shippo_prepare_address(order.partner_shipping_id)
        parcel = self._shippo_prepare_parcel(order._get_estimated_weight())

        shipment = sr.create_shipment(shipper, recipient, parcel)
        rate = sr.lowest_rate(shipment)
        price = self._get_price_currency(float(rate["amount"]), rate.get("currency", "USD"), order)

        return {
            "success": True,
            "price": price,
            "error_message": False,
            "warning_message": False,
            "shippo_shipment_id": shipment.get("object_id"),
            "shippo_rate_id": rate.get("object_id"),
        }

    # -- send_shipping --------------------------------------------------
    # TODO: only handles one package per picking for now. delivery_easypost_oca
    # splits by result_package_id and threads multiple shipments (see its
    # _prepare_shipments / create_multiples_shipments). Port that if Helm
    # ever ships multi-package orders through this carrier -- not needed for
    # the current single-box peptide/label fulfillment flow.
    def shippo_send_shipping(self, pickings):
        sr = ShippoRequest(self)
        res = []
        for picking in pickings:
            shipper = self._shippo_prepare_address(
                picking.picking_type_id.warehouse_id.partner_id
            )
            recipient = self._shippo_prepare_address(picking.partner_id)
            weight = sum(
                ml.product_id.weight * ml.quantity for ml in picking.move_line_ids
            )
            parcel = self._shippo_prepare_parcel(weight)

            shipment = sr.create_shipment(shipper, recipient, parcel)
            rate = sr.lowest_rate(shipment)
            bought = sr.buy_rate(rate, self.shippo_label_file_type)

            price = self._get_price_currency(bought.rate, bought.currency, picking.sale_id)

            picking.write(
                {
                    "shippo_shipment_id": bought.shipment_id,
                    "shippo_tracking_number": bought.tracking_code,
                    "shippo_tracking_url": bought.tracking_url,
                    "shippo_carrier_name": bought.carrier_name,
                    "shippo_carrier_service": bought.carrier_service,
                }
            )
            picking.message_post(
                body=_("Shipment purchased via Shippo. Tracking: %s") % bought.tracking_code,
                attachments=[(f"Label-{picking.name}.pdf", bought.get_label_content())],
            )

            res.append({"exact_price": price, "tracking_number": bought.tracking_code})
        return res

    def shippo_get_tracking_link(self, picking):
        return picking.shippo_tracking_url

    def shippo_cancel_shipment(self, pickings):
        sr = ShippoRequest(self)
        for picking in pickings:
            if not picking.shippo_shipment_id:
                continue
            sr.refund_transaction(picking.shippo_shipment_id)
            picking.message_post(body=_("Shippo label voided/refund requested."))

    # -- helpers --------------------------------------------------------
    def _shippo_prepare_address(self, addr_obj):
        if not addr_obj.phone or not addr_obj.email:
            raise UserError(
                _(
                    "%(name)s is missing a phone or email. Shippo silently drops "
                    "carriers from rate shopping (and USPS label purchase hard-fails) "
                    "when address_from lacks phone+email -- confirmed 2026-08-11 probe."
                )
                % {"name": addr_obj.display_name}
            )
        address = {
            "name": addr_obj.name or addr_obj.display_name,
            "street1": addr_obj.street or "",
            "city": addr_obj.city or "",
            "zip": addr_obj.zip or "",
            "country": addr_obj.country_id.code,
            "phone": addr_obj.phone,
            "email": addr_obj.email,
        }
        if addr_obj.street2:
            address["street2"] = addr_obj.street2
        if addr_obj.state_id:
            address["state"] = addr_obj.state_id.code
        return address

    def _shippo_prepare_parcel(self, weight_kg: float) -> dict:
        # TODO: pull real parcel dims from a stock.package.type once one is
        # defined for Helm's shipping boxes/label mailers, same pattern as
        # delivery_easypost_oca's _prepare_parcel + stock_package_type.py.
        # Hardcoded placeholder dims below -- do not ship live on these.
        weight_lb = weight_kg * 2.20462
        return {
            "length": "6",
            "width": "4",
            "height": "2",
            "distance_unit": "in",
            "weight": str(round(max(weight_lb, 0.1), 2)),
            "mass_unit": "lb",
        }

    def _get_price_currency(self, rate: float, currency: str, order=False) -> float:
        price = float(rate)
        currency_id = order.currency_id if order else self.env.company.currency_id
        if currency_id.name != currency:
            quote_currency = self.env["res.currency"].search([("name", "=", currency)], limit=1)
            price = quote_currency._convert(price, currency_id, self.env.company, fields.Date.today())
        return price
