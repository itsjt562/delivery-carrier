from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    shippo_shipment_id = fields.Char(copy=False)
    shippo_tracking_number = fields.Char(copy=False)
    shippo_tracking_url = fields.Char(copy=False)
    shippo_carrier_name = fields.Char(copy=False)
    shippo_carrier_service = fields.Char(copy=False)
