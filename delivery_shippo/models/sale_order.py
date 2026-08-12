from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    shippo_shipment_id = fields.Char(tracking=False, copy=False)
    shippo_rate_id = fields.Char(tracking=False, copy=False)
