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

        cls.carrier = cls.env["delivery.carrier"].create(
            {
                "name": "SHIPPO",
                "delivery_type": "shippo",
                "shippo_test_api_key": SHIPPO_TEST_KEY,
                "shippo_label_file_type": "PDF",
                "product_id": cls.delivery_product.id,
            }
        )

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
