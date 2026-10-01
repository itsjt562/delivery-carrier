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
    shippo_default_package_type_id = fields.Many2one(
        "stock.package.type",
        string="Default Packaging",
        domain="[('package_carrier_type', 'in', ('none', 'shippo'))]",
        help="Dimensions used when a delivery order does not name its own "
        "packaging. Set this to whatever most orders ship in, so the common "
        "case needs no input at all and only the unusual parcel needs a choice.",
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
        parcel = self._shippo_prepare_parcel(
            self._shippo_order_weight(order), self._shippo_package_type()
        )

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
            parcel = self._shippo_prepare_parcel(
                self._shippo_picking_weight(picking),
                self._shippo_package_type(picking),
            )

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

        Two rungs, most specific first, and then a refusal:

        1. `shippo_label_weight`, read off a scale after packing and typed on
           the delivery order. The only rung that works in a one-step
           `ship_only` warehouse with the Packages feature off, which is the
           common case for single-box fulfilment.
        2. `picking.shipping_weight`, which resolves a Put in Pack override on
           `stock.quant.package.shipping_weight`. Costs nothing to support and
           means enabling Packages later needs no change here.

        WHY THERE IS NO PRODUCT-WEIGHT FALLBACK

        There used to be, and it was the quiet failure. A catalogue whose
        weights are unset sums to zero, the parcel lands on a floor weight, and
        the carrier bills the difference weeks later against a label that
        already shipped. A missing weight is a question for a human, not a
        number to invent, so this raises instead.
        """
        weight = picking.shippo_label_weight or picking.shipping_weight
        if not weight:
            raise UserError(
                _(
                    "No shipping weight is set on %(picking)s, so no label was "
                    "bought.\n\n"
                    "Weigh the packed parcel and enter it as Label Weight on this "
                    "delivery order, then validate again."
                )
                % {"picking": picking.name}
            )
        return weight

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

    # -- parcel ---------------------------------------------------------
    # The dimensions used to be three hardcoded constants describing one
    # bubble mailer. That is fine right up until something ships in a box, at
    # which point the carrier is quoted and billed against a parcel that does
    # not exist. Dimensional weight means wrong dimensions cost real money
    # even when the scale weight is right.

    def _shippo_package_type(self, picking=None):
        """Which packaging to declare, most specific first.

        Refuses rather than guessing. A default parcel size invented here
        would be wrong silently and chargeable, which is the failure mode this
        replaced.
        """
        self.ensure_one()
        package_type = (picking and picking.shippo_package_type_id) or (
            self.shippo_default_package_type_id
        )
        if not package_type:
            raise UserError(
                _(
                    "No packaging is set, so there are no dimensions to quote.\n\n"
                    "Set Default Packaging on the %(carrier)s delivery method, or "
                    "pick a packaging on this delivery order."
                )
                % {"carrier": self.name}
            )
        return package_type

    def _shippo_prepare_parcel(self, weight, package_type) -> dict:
        """Build the parcel Shippo is quoted and billed on.

        `weight` is the TOTAL declared weight in the database's weight UoM, not
        a contents weight to which packaging is then added. Callers decide what
        total means: the label path uses the number somebody read off a scale
        after packing, the quote path uses an estimate. Adding packaging weight
        here would double-count the first and is the caller's job in the second.

        Both unit conversions are explicit rather than assumed, because the
        database can be configured in kg or lb and in mm, inches or feet, while
        Shippo is always quoted in lb and inches.
        """
        product_tmpl = self.env["product.template"]
        weight_uom = product_tmpl._get_weight_uom_id_from_ir_config_parameter()
        length_uom = product_tmpl._get_length_uom_id_from_ir_config_parameter()
        inch = self.env.ref("uom.product_uom_inch")

        # Shippo rejects a zero-weight parcel outright.
        weight_lb = max(
            weight_uom._compute_quantity(weight, self.env.ref("uom.product_uom_lb")),
            0.1,
        )

        def to_inches(value):
            return str(round(length_uom._compute_quantity(value, inch), 2))

        return {
            "length": to_inches(package_type.packaging_length),
            "width": to_inches(package_type.width),
            "height": to_inches(package_type.height),
            "distance_unit": "in",
            "weight": str(round(weight_lb, 2)),
            "mass_unit": "lb",
        }

    def _get_price_currency(self, rate: float, currency: str, order=False) -> float:
        price = float(rate)
        currency_id = order.currency_id if order else self.env.company.currency_id
        if currency_id.name != currency:
            quote_currency = self.env["res.currency"].search([("name", "=", currency)], limit=1)
            price = quote_currency._convert(price, currency_id, self.env.company, fields.Date.today())
        return price
