from odoo import fields, models


class StockPackageType(models.Model):
    _inherit = "stock.package.type"

    # Lets a package type be filtered to this carrier, the same way
    # delivery_easypost_oca registers itself. Without it every package type in
    # the database shows up in the Shippo pickers, including ones that only
    # make sense for another integration.
    package_carrier_type = fields.Selection(
        selection_add=[("shippo", "Shippo")],
        ondelete={"shippo": "set default"},
    )
