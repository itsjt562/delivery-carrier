from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase

# Fake -- create_shipment/buy_rate/refund_transaction are all mocked in
# these tests, so this value is never actually sent over the network.
SHIPPO_TEST_KEY = "shippo_test_0000000000000000000000000000000000000000"


@tagged("post_install", "-at_install")
class ShippoTestBaseCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        usd_currency = cls.env.ref("base.USD")
        cls.env.company.currency_id = usd_currency

        # order.warehouse_id.partner_id resolves to the company's own partner
        # record by default -- write phone+email onto that directly rather
        # than swapping in a new partner, so the default test warehouse
        # actually picks it up. Deliberately non-empty: _shippo_prepare_address
        # raises UserError without them (2026-08-11 probe finding: Shippo
        # silently drops carriers from rate shopping, then hard-fails at
        # label purchase, if address_from lacks either).
        cls.company_partner = cls.env.company.partner_id
        cls.company_partner.write(
            {
                "street": "215 Clayton St.",
                "city": "San Francisco",
                "state_id": cls.env.ref("base.state_us_5").id,
                "zip": "94117",
                "country_id": cls.env.ref("base.us").id,
                "phone": "5555550100",
                "email": "ship@helmcompounds.test",
            }
        )

        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Test Customer",
                "street": "965 Mission St.",
                "city": "San Francisco",
                "state_id": cls.env.ref("base.state_us_5").id,
                "zip": "94105",
                "country_id": cls.env.ref("base.us").id,
                "phone": "5555550101",
                "email": "customer@test.com",
            }
        )

        product_sudo = cls.env["product.product"]
        cls.product = product_sudo.create(
            {"name": "Test Peptide Vial", "type": "consu", "weight": 0.25}
        )
        cls.delivery_product = product_sudo.create(
            {
                "name": "Shippo Delivery",
                "type": "service",
                "categ_id": cls.env.ref("product.product_category_all").id,
            }
        )

        # Dimensions are now required rather than hardcoded, so every test
        # needs real packaging. Deliberately not square, so a test that mixed
        # up length/width/height would fail rather than coincidentally pass.
        cls.package_type = cls.env["stock.package.type"].create(
            {
                "name": "Test Bubble Mailer",
                "packaging_length": 12.0,
                "width": 8.0,
                "height": 1.0,
            }
        )
        cls.big_box = cls.env["stock.package.type"].create(
            {
                "name": "Test Big Box",
                "packaging_length": 20.0,
                "width": 16.0,
                "height": 10.0,
            }
        )

        cls.carrier = cls.env["delivery.carrier"].create(
            {
                "name": "SHIPPO",
                "delivery_type": "shippo",
                "shippo_test_api_key": SHIPPO_TEST_KEY,
                "shippo_label_file_type": "PDF",
                "product_id": cls.delivery_product.id,
                "shippo_default_package_type_id": cls.package_type.id,
            }
        )

    def _ready_picking(self, sale_order, weight=1.0):
        """A picking packed and weighed, which is now the precondition for a
        label rather than something the module invents."""
        picking = sale_order.picking_ids[0]
        picking.action_assign()
        picking.move_line_ids.write({"quantity": 1})
        if weight is not None:
            picking.shippo_label_weight = weight
        return picking

    def _create_sale_order(self, qty=1):
        order_form = Form(self.env["sale.order"])
        order_form.partner_id = self.partner
        with order_form.order_line.new() as line_form:
            line_form.product_id = self.product
            line_form.product_uom_qty = qty
        sale = order_form.save()
        delivery_wizard = Form(
            self.env["choose.delivery.carrier"].with_context(
                default_order_id=sale.id,
                default_carrier_id=self.carrier.id,
            )
        ).save()
        delivery_wizard.button_confirm()
        sale.action_confirm()
        return sale
