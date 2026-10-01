from odoo import api, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.model
    def _get_length_uom_id_from_ir_config_parameter(self):
        """Allow inches as the length unit.

        Core offers exactly two options: millimetres by default, or feet when
        `product.volume_in_cubic_feet` is "1". Neither suits a US domestic
        shipper. A 8x12x1 inch mailer is 203x305x25 in millimetres, or
        0.667x1x0.083 in feet, and both of those are numbers somebody has to
        convert before typing and convert back to check.

        Shippo is quoted in inches anyway, so an inch-configured database
        hands the dimensions straight through with no conversion step to get
        wrong. Opt-in, off unless `product.length_in_inches` is "1", so this
        changes nothing for an installation that has not asked for it.

        Set BEFORE any dimension is recorded. The parameter changes how stored
        values are interpreted, it does not convert them, so flipping it later
        silently rescales every length in the database.
        """
        if (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("product.length_in_inches")
            == "1"
        ):
            return self.env.ref("uom.product_uom_inch")
        return super()._get_length_uom_id_from_ir_config_parameter()
