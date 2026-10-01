from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    shippo_shipment_id = fields.Char(copy=False)
    shippo_tracking_number = fields.Char(copy=False)
    shippo_tracking_url = fields.Char(copy=False)
    shippo_carrier_name = fields.Char(copy=False)
    shippo_carrier_service = fields.Char(copy=False)

    # WHY A NEW FIELD RATHER THAN REUSING ONE OF THE TWO THAT LOOK RIGHT
    #
    # Neither of the obvious candidates can hold a number somebody types on a
    # delivery order:
    #
    #   stock.picking.weight           computed and stored from product
    #                                  weights, readonly. Correcting it means
    #                                  editing the catalogue.
    #   stock.picking.shipping_weight  computed, store=False, readonly=False
    #                                  and with no inverse, so the form accepts
    #                                  a value and then silently drops it on
    #                                  save. Worse than readonly, because it
    #                                  looks like it worked.
    #
    # Odoo's own answer is the Put in Pack flow, which writes
    # stock.quant.package.shipping_weight. That requires the Packages feature
    # (stock.group_tracking_lot), which is off here, and turning it on adds a
    # packing step to every single-box order to capture one number.
    #
    # So: one stored, writable float on the record the picker is already
    # looking at. _shippo_picking_weight reads it first and still honours the
    # package path underneath, so enabling Packages later changes nothing here.
    shippo_package_type_id = fields.Many2one(
        "stock.package.type",
        string="Packaging",
        copy=False,
        domain="[('package_carrier_type', 'in', ('none', 'shippo'))]",
        help="The box or mailer this parcel actually ships in. Its dimensions "
        "are what the carrier quotes and bills against, so a wrong size costs "
        "real money through dimensional weight even when the scale weight is "
        "right. Leave empty to use the delivery method's default packaging.",
    )
    shippo_label_weight = fields.Float(
        "Label Weight",
        copy=False,
        help="Weight to declare on the shipping label, in this database's "
        "weight unit. Set it after packing and before validating, since "
        "validating is what buys the label. Leave at zero to fall back to the "
        "packed weight, or to the sum of the product weights.",
    )
