from odoo import _, fields, models
from odoo.exceptions import UserError

from .shippo_request import ShippoRequest

# Shippo `servicelevel.token` values, read off a real rate response from the
# Sacramento origin on 2026-10-01 rather than transcribed from docs. A
# Selection rather than a free-text Char on purpose: a mistyped token would not
# fail at configuration time, it would fail at label purchase on a paid order,
# which is the worst possible moment to discover a typo. Adding a service means
# extending this list, which is a one-line change and a module upgrade.
SHIPPO_SERVICE_LEVELS = [
    ("usps_ground_advantage", "USPS Ground Advantage"),
    ("usps_priority", "USPS Priority Mail"),
    ("usps_priority_express", "USPS Priority Mail Express"),
    ("ups_ground_saver", "UPS Ground Saver"),
    ("ups_ground", "UPS Ground"),
    ("ups_3_day_select", "UPS 3 Day Select"),
    ("ups_second_day_air", "UPS 2nd Day Air"),
    ("ups_second_day_air_am", "UPS 2nd Day Air A.M."),
    ("ups_next_day_air_saver", "UPS Next Day Air Saver"),
    ("ups_next_day_air", "UPS Next Day Air"),
    ("ups_next_day_air_early_am", "UPS Next Day Air Early"),
]


class DeliveryCarrier(models.Model):
    _inherit = "delivery.carrier"

    # Shippo's label_file_type tokens don't match file extensions 1:1
    # (ZPLII -> .zpl) -- attachment filenames need the real extension or
    # the chatter attachment is mislabeled/unopenable as what it claims to be.
    _SHIPPO_LABEL_EXTENSIONS = {"PDF": "pdf", "PNG": "png", "ZPLII": "zpl"}

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
        default="ZPLII",
        help="ZPLII renders at 812x1219 dots @ 203dpi -- the standard 4x6in "
        "thermal shipping label size, confirmed against a real sandbox label.",
    )
    shippo_service_level_token = fields.Selection(
        SHIPPO_SERVICE_LEVELS,
        string="Service Level",
        help="The one carrier service this delivery method buys. Leave empty to "
        "buy whichever rate is cheapest, which is only appropriate when nothing "
        "has promised the customer a transit time. Set it for any method sold at "
        "a flat price under a named tier: a storefront tier that names a service "
        "and a label bought at the cheapest rate are two different promises.",
    )

    # -- rate_shipment ------------------------------------------------------
    def shippo_rate_shipment(self, order):
        sr = ShippoRequest(self)
        shipper = self._shippo_prepare_address(order.warehouse_id.partner_id)
        recipient = self._shippo_prepare_address(order.partner_shipping_id)
        parcel = self._shippo_prepare_parcel(self._shippo_order_weight(order))

        shipment = sr.create_shipment(shipper, recipient, parcel)
        rate = self._shippo_select_rate(sr, shipment)
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
            parcel = self._shippo_prepare_parcel(self._shippo_picking_weight(picking))

            shipment = sr.create_shipment(shipper, recipient, parcel)
            rate = self._shippo_select_rate(sr, shipment)
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
            label_ext = self._SHIPPO_LABEL_EXTENSIONS.get(
                self.shippo_label_file_type, self.shippo_label_file_type.lower()
            )
            picking.message_post(
                body=_("Shipment purchased via Shippo. Tracking: %s") % bought.tracking_code,
                attachments=[
                    (f"Label-{picking.name}.{label_ext}", bought.get_label_content())
                ],
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

    # -- weight ---------------------------------------------------------
    # Both of these existed as inline sums over product weights, which meant
    # the module quoted and shipped the computed product total and nothing
    # else. Odoo offers two places to correct a weight by hand and neither of
    # them reached Shippo, so a parcel whose real weight differed from the
    # catalogue sum could not be fixed anywhere in the UI.

    def _shippo_order_weight(self, order):
        """Quote-time weight, in the company weight UoM.

        Same precedence core documents in `delivery/models/delivery_carrier.py`:
        the weight typed into the delivery wizard arrives as `order_weight` in
        the context, then the weight saved on the order, then the computed
        order-line total.
        """
        return (
            self.env.context.get("order_weight")
            or order.shipping_weight
            or order._get_estimated_weight()
        )

    def _shippo_picking_weight(self, picking):
        """Label-time weight, in the company weight UoM.

        `picking.shipping_weight` already resolves a Put in Pack override
        (`stock.quant.package.shipping_weight`) ahead of the computed product
        total, so reading it is what lets a hand-entered weight reach the label.
        It falls back to the product sum, which keeps the previous behaviour
        exactly when nothing has been overridden.
        """
        return picking.shipping_weight or sum(
            ml.product_id.weight * ml.quantity for ml in picking.move_line_ids
        )

    # -- helpers --------------------------------------------------------
    def _shippo_select_rate(self, shippo_request, shipment):
        """Which of the returned rates this carrier actually sells.

        One place, used by both rate_shipment and send_shipping, so a quote and
        the label bought against it can never disagree about the service.
        """
        self.ensure_one()
        if self.shippo_service_level_token:
            return shippo_request.rate_for_service(
                shipment, self.shippo_service_level_token
            )
        return shippo_request.lowest_rate(shipment)

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

    # Helm ships everything in one packaging format -- confirmed with Joseph
    # 2026-08-12: 8x12x1in bubble mailer, ~8oz packaging weight (mailer +
    # insert), flat regardless of order contents. No stock.package.type
    # variation needed unless that changes.
    _MAILER_LENGTH_IN = "12"
    _MAILER_WIDTH_IN = "8"
    _MAILER_HEIGHT_IN = "1"
    _MAILER_WEIGHT_LB = 0.5  # 8 oz

    def _shippo_prepare_parcel(self, weight) -> dict:
        # weight arrives in Odoo's configured weight UoM (kg by default,
        # lb if product.weight_in_lbs is set) -- convert explicitly rather
        # than assume, same pattern as delivery_easypost_oca's
        # _easypost_oca_convert_weight.
        weight_uom_id = self.env[
            "product.template"
        ]._get_weight_uom_id_from_ir_config_parameter()
        weight_lb = weight_uom_id._compute_quantity(weight, self.env.ref("uom.product_uom_lb"))
        total_lb = max(weight_lb + self._MAILER_WEIGHT_LB, 0.1)
        return {
            "length": self._MAILER_LENGTH_IN,
            "width": self._MAILER_WIDTH_IN,
            "height": self._MAILER_HEIGHT_IN,
            "distance_unit": "in",
            "weight": str(round(total_lb, 2)),
            "mass_unit": "lb",
        }

    def _get_price_currency(self, rate: float, currency: str, order=False) -> float:
        price = float(rate)
        currency_id = order.currency_id if order else self.env.company.currency_id
        if currency_id.name != currency:
            quote_currency = self.env["res.currency"].search([("name", "=", currency)], limit=1)
            price = quote_currency._convert(price, currency_id, self.env.company, fields.Date.today())
        return price
