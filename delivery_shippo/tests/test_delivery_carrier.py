from unittest.mock import patch

from odoo.exceptions import UserError

from odoo.addons.delivery_shippo.models.shippo_request import ShippoRequest

from .common import ShippoTestBaseCase
from .mock_shippo import (
    create_mock_bought_shipment,
    load_shipment_response,
    mock_requests_get_label,
)


class TestDeliveryCarrier(ShippoTestBaseCase):
    """Unit tests for delivery_shippo using mocked Shippo API calls.

    create_shipment/buy_rate/refund_transaction are mocked (no network);
    lowest_rate() and _shippo_prepare_address() run for real against the
    fixture data, since those are our own logic, not Shippo's.
    """

    @patch.object(ShippoRequest, "create_shipment")
    def test_shippo_rate_shipment(self, mock_create_shipment):
        mock_create_shipment.return_value = load_shipment_response()

        order = self._create_sale_order(qty=1)
        res = self.carrier.shippo_rate_shipment(order)

        self.assertTrue(res["success"])
        self.assertGreater(res["price"], 0)
        # Fixture's cheapest rate is USPS Ground Advantage at 5.17.
        self.assertAlmostEqual(res["price"], 5.17, places=2)

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_shippo_send_shipping(self, mock_buy_rate, mock_create_shipment, mock_requests_get):
        mock_requests_get.return_value = mock_requests_get_label()
        mock_create_shipment.return_value = load_shipment_response()
        mock_buy_rate.return_value = create_mock_bought_shipment(
            tracking_code="TRACK_DEFAULT_123",
            rate=5.17,
            tracking_url="https://tools.usps.com/go/TrackConfirmAction_input?origTrackNum=TRACK_DEFAULT_123",
        )

        # Rate shopping during order confirmation also hits create_shipment
        # via the same mock -- fine, that's the point of mocking the class
        # method rather than the HTTP layer.
        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order)

        picking._action_done()

        self.assertGreater(picking.carrier_price, 0.0)
        self.assertEqual(picking.carrier_tracking_ref, "TRACK_DEFAULT_123")
        self.assertEqual(picking.shippo_tracking_number, "TRACK_DEFAULT_123")
        self.assertIn("TRACK_DEFAULT_123", picking.shippo_tracking_url)
        self.assertEqual(picking.shippo_carrier_name, "USPS")

        # Regression test: attachment filename must match the real label
        # format, not always ".pdf" regardless of shippo_label_file_type.
        label_message = picking.message_ids.filtered(lambda m: m.attachment_ids)
        self.assertTrue(label_message, "No label attachment posted to the chatter")
        attachment = label_message.attachment_ids[0]
        self.assertTrue(
            attachment.name.endswith(f".{self.carrier._SHIPPO_LABEL_EXTENSIONS['PDF']}"),
            f"Expected a .pdf attachment for label_file_type=PDF, got {attachment.name}",
        )

        # get_tracking_link should surface exactly what was written
        self.assertEqual(
            self.carrier.shippo_get_tracking_link(picking), picking.shippo_tracking_url
        )

        mock_create_shipment.assert_called()
        mock_buy_rate.assert_called_once()

    @patch.object(ShippoRequest, "refund_transaction")
    def test_shippo_cancel_shipment(self, mock_refund):
        mock_refund.return_value = {"status": "SUCCESS"}

        sale_order = self._create_sale_order(qty=1)
        picking = sale_order.picking_ids[0]
        picking.shippo_shipment_id = "15d63f515241474cbb969c58dbf26b10"

        self.carrier.shippo_cancel_shipment(picking)

        mock_refund.assert_called_once_with("15d63f515241474cbb969c58dbf26b10")

    def test_shippo_prepare_address_requires_phone_and_email(self):
        """Regression test for the 2026-08-11 probe finding: Shippo doesn't
        error on a phone/email-less address_from, it silently drops carriers
        from rate shopping and hard-fails at label purchase. We turn that
        into a loud UserError before ever calling the API.
        """
        no_contact_partner = self.env["res.partner"].create(
            {
                "name": "No Contact Info",
                "street": "1 Nowhere Ln",
                "city": "San Francisco",
                "zip": "94105",
                "country_id": self.env.ref("base.us").id,
                "phone": False,
                "email": False,
            }
        )
        with self.assertRaises(UserError):
            self.carrier._shippo_prepare_address(no_contact_partner)


class TestShippoServiceLevel(ShippoTestBaseCase):
    """A carrier that names a service must buy that service or refuse.

    The fixture's rate list is a real captured response carrying all eleven
    service levels, with USPS Ground Advantage cheapest at 5.17 and UPS 2nd Day
    Air at 10.76. That gap is what makes these assertions meaningful: every one
    of them would also pass if the code simply sorted by price, except that the
    expected value would be the wrong number.
    """

    SECOND_DAY = "ups_second_day_air"
    SECOND_DAY_AMOUNT = 10.76
    CHEAPEST_AMOUNT = 5.17

    @patch.object(ShippoRequest, "create_shipment")
    def test_rate_shipment_honours_the_pinned_service(self, mock_create_shipment):
        mock_create_shipment.return_value = load_shipment_response()
        self.carrier.shippo_service_level_token = self.SECOND_DAY

        order = self._create_sale_order(qty=1)
        res = self.carrier.shippo_rate_shipment(order)

        self.assertTrue(res["success"])
        self.assertAlmostEqual(res["price"], self.SECOND_DAY_AMOUNT, places=2)
        self.assertNotAlmostEqual(res["price"], self.CHEAPEST_AMOUNT, places=2)

    @patch.object(ShippoRequest, "create_shipment")
    def test_rate_shipment_falls_back_to_cheapest_with_no_service_set(
        self, mock_create_shipment
    ):
        """An empty token keeps the pre-existing behaviour, which is what the
        one already-configured production carrier relies on."""
        mock_create_shipment.return_value = load_shipment_response()
        self.assertFalse(self.carrier.shippo_service_level_token)

        order = self._create_sale_order(qty=1)
        res = self.carrier.shippo_rate_shipment(order)

        self.assertAlmostEqual(res["price"], self.CHEAPEST_AMOUNT, places=2)

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_send_shipping_buys_the_pinned_service(
        self, mock_buy_rate, mock_create_shipment, mock_requests_get
    ):
        mock_requests_get.return_value = mock_requests_get_label()
        mock_create_shipment.return_value = load_shipment_response()
        mock_buy_rate.return_value = create_mock_bought_shipment(
            tracking_code="TRACK_2DAY",
            rate=self.SECOND_DAY_AMOUNT,
            carrier_name="UPS",
            carrier_service="2nd Day Air",
        )
        self.carrier.shippo_service_level_token = self.SECOND_DAY

        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order)
        picking._action_done()

        # The assertion that matters: the rate handed to buy_rate is the pinned
        # service, not the cheapest one in the same response.
        bought_rate = mock_buy_rate.call_args[0][0]
        self.assertEqual(bought_rate["servicelevel"]["token"], self.SECOND_DAY)
        self.assertAlmostEqual(
            float(bought_rate["amount"]), self.SECOND_DAY_AMOUNT, places=2
        )
        self.assertEqual(picking.shippo_carrier_service, "2nd Day Air")

    @patch.object(ShippoRequest, "create_shipment")
    def test_missing_service_raises_and_names_what_was_offered(
        self, mock_create_shipment
    ):
        """A destination that cannot take the sold service must stop the label,
        not quietly downgrade it. Simulated by removing that one rate from an
        otherwise real response.
        """
        response = load_shipment_response()
        response["rates"] = [
            r
            for r in response["rates"]
            if (r.get("servicelevel") or {}).get("token") != self.SECOND_DAY
        ]
        self.assertTrue(response["rates"], "fixture should still offer other services")
        mock_create_shipment.return_value = response
        self.carrier.shippo_service_level_token = self.SECOND_DAY

        order = self._create_sale_order(qty=1)
        with self.assertRaises(UserError) as caught:
            self.carrier.shippo_rate_shipment(order)

        message = str(caught.exception)
        self.assertIn(self.SECOND_DAY, message)
        # The offered list is the actionable part -- without it the error says
        # only that something is wrong.
        self.assertIn("usps_ground_advantage", message)

    @patch.object(ShippoRequest, "create_shipment")
    def test_no_rates_at_all_raises(self, mock_create_shipment):
        response = load_shipment_response()
        response["rates"] = []
        mock_create_shipment.return_value = response
        self.carrier.shippo_service_level_token = self.SECOND_DAY

        order = self._create_sale_order(qty=1)
        with self.assertRaises(UserError):
            self.carrier.shippo_rate_shipment(order)


class TestShippoWeightOverrides(ShippoTestBaseCase):
    """A weight typed by a human has to reach Shippo.

    Both API paths used to sum product weights inline, so neither of Odoo's two
    hand-override points reached the parcel. The test product weighs 0.25 in
    the company weight UoM, so every expectation here is a different number
    from the catalogue default, which is the only way to tell a honoured
    override from an ignored one.
    """

    def _expected_parcel_weight(self, weight_in_company_uom):
        """What _shippo_prepare_parcel should produce for a given input weight.

        Mirrors the conversion deliberately. What is under test is which weight
        gets selected, not whether the unit conversion is correct. The weight
        passed in is the total, so nothing is added for packaging.
        """
        uom = self.env["product.template"]._get_weight_uom_id_from_ir_config_parameter()
        in_lb = uom._compute_quantity(
            weight_in_company_uom, self.env.ref("uom.product_uom_lb")
        )
        return str(round(max(in_lb, 0.1), 2))

    @staticmethod
    def _parcel_sent(mock_create_shipment):
        # create_shipment(shipper, recipient, parcel)
        return mock_create_shipment.call_args[0][2]

    @patch.object(ShippoRequest, "create_shipment")
    def test_rate_uses_the_order_line_total_by_default(self, mock_create_shipment):
        mock_create_shipment.return_value = load_shipment_response()

        order = self._create_sale_order(qty=1)
        self.carrier.shippo_rate_shipment(order)

        self.assertEqual(
            self._parcel_sent(mock_create_shipment)["weight"],
            self._expected_parcel_weight(0.25),
        )

    @patch.object(ShippoRequest, "create_shipment")
    def test_rate_honours_the_weight_saved_on_the_order(self, mock_create_shipment):
        mock_create_shipment.return_value = load_shipment_response()

        order = self._create_sale_order(qty=1)
        order.shipping_weight = 2.0

        self.carrier.shippo_rate_shipment(order)

        self.assertEqual(
            self._parcel_sent(mock_create_shipment)["weight"],
            self._expected_parcel_weight(2.0),
        )

    @patch.object(ShippoRequest, "create_shipment")
    def test_rate_honours_the_wizard_weight_from_context(self, mock_create_shipment):
        """Highest precedence: what someone typed into the delivery wizard,
        which core passes down as the order_weight context key."""
        mock_create_shipment.return_value = load_shipment_response()

        order = self._create_sale_order(qty=1)
        order.shipping_weight = 2.0

        self.carrier.with_context(order_weight=5.0).shippo_rate_shipment(order)

        self.assertEqual(
            self._parcel_sent(mock_create_shipment)["weight"],
            self._expected_parcel_weight(5.0),
        )

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_label_honours_the_picking_shipping_weight(
        self, mock_buy_rate, mock_create_shipment, mock_requests_get
    ):
        """The Put in Pack override reaches the label.

        picking.shipping_weight resolves stock.quant.package.shipping_weight
        ahead of the computed total, so setting it on the package is the
        supported way to correct a label weight by hand.
        """
        mock_requests_get.return_value = mock_requests_get_label()
        mock_create_shipment.return_value = load_shipment_response()
        mock_buy_rate.return_value = create_mock_bought_shipment()

        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order, weight=None)
        package = picking._put_in_pack(picking.move_line_ids)
        package.shipping_weight = 3.0

        picking._action_done()

        self.assertEqual(
            self._parcel_sent(mock_create_shipment)["weight"],
            self._expected_parcel_weight(3.0),
        )

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_label_honours_the_weight_typed_on_the_delivery_order(
        self, mock_buy_rate, mock_create_shipment, mock_requests_get
    ):
        """The rung that matters in a one-step warehouse with Packages off,
        which is the configuration this is actually deployed into."""
        mock_requests_get.return_value = mock_requests_get_label()
        mock_create_shipment.return_value = load_shipment_response()
        mock_buy_rate.return_value = create_mock_bought_shipment()

        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order, weight=4.0)

        picking._action_done()

        self.assertEqual(
            self._parcel_sent(mock_create_shipment)["weight"],
            self._expected_parcel_weight(4.0),
        )

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_typed_weight_beats_the_package_weight(
        self, mock_buy_rate, mock_create_shipment, mock_requests_get
    ):
        """Precedence, not just presence. Someone who weighed the finished box
        and typed it in outranks whatever the packing step recorded."""
        mock_requests_get.return_value = mock_requests_get_label()
        mock_create_shipment.return_value = load_shipment_response()
        mock_buy_rate.return_value = create_mock_bought_shipment()

        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order, weight=4.0)
        package = picking._put_in_pack(picking.move_line_ids)
        package.shipping_weight = 3.0

        picking._action_done()

        self.assertEqual(
            self._parcel_sent(mock_create_shipment)["weight"],
            self._expected_parcel_weight(4.0),
        )


class TestShippoParcelDimensions(ShippoTestBaseCase):
    """Dimensions are quoted and billed on, so they have to be real.

    They used to be three constants describing one bubble mailer, which is
    correct until something ships in a box and then silently wrong in a way
    dimensional weight turns into money.
    """

    def _expected_inches(self, value):
        product_tmpl = self.env["product.template"]
        length_uom = product_tmpl._get_length_uom_id_from_ir_config_parameter()
        return str(
            round(
                length_uom._compute_quantity(
                    value, self.env.ref("uom.product_uom_inch")
                ),
                2,
            )
        )

    @staticmethod
    def _parcel_sent(mock_create_shipment):
        return mock_create_shipment.call_args[0][2]

    @patch.object(ShippoRequest, "create_shipment")
    def test_quote_uses_the_carrier_default_packaging(self, mock_create_shipment):
        mock_create_shipment.return_value = load_shipment_response()

        order = self._create_sale_order(qty=1)
        self.carrier.shippo_rate_shipment(order)

        parcel = self._parcel_sent(mock_create_shipment)
        self.assertEqual(parcel["length"], self._expected_inches(12.0))
        self.assertEqual(parcel["width"], self._expected_inches(8.0))
        self.assertEqual(parcel["height"], self._expected_inches(1.0))
        self.assertEqual(parcel["distance_unit"], "in")
        self.assertEqual(parcel["mass_unit"], "lb")

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_picking_packaging_overrides_the_carrier_default(
        self, mock_buy_rate, mock_create_shipment, mock_requests_get
    ):
        """The whole point of the per-picking field: one order goes in a box
        while the default stays the mailer."""
        mock_requests_get.return_value = mock_requests_get_label()
        mock_create_shipment.return_value = load_shipment_response()
        mock_buy_rate.return_value = create_mock_bought_shipment()

        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order)
        picking.shippo_package_type_id = self.big_box

        picking._action_done()

        parcel = self._parcel_sent(mock_create_shipment)
        self.assertEqual(parcel["length"], self._expected_inches(20.0))
        self.assertEqual(parcel["width"], self._expected_inches(16.0))
        self.assertEqual(parcel["height"], self._expected_inches(10.0))

    @patch.object(ShippoRequest, "create_shipment")
    def test_no_packaging_anywhere_raises(self, mock_create_shipment):
        mock_create_shipment.return_value = load_shipment_response()
        self.carrier.shippo_default_package_type_id = False

        order = self._create_sale_order(qty=1)
        with self.assertRaises(UserError) as caught:
            self.carrier.shippo_rate_shipment(order)
        self.assertIn("packaging", str(caught.exception).lower())


class TestShippoWeightIsRequired(ShippoTestBaseCase):
    """A missing weight is a question for a human, not a number to invent."""

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_label_without_a_weight_raises_and_buys_nothing(
        self, mock_buy_rate, mock_create_shipment, mock_requests_get
    ):
        """The regression that matters most. The old code summed product
        weights, got zero from an unweighed catalogue, and shipped on a floor
        weight the carrier later billed the difference on.
        """
        mock_requests_get.return_value = mock_requests_get_label()
        mock_create_shipment.return_value = load_shipment_response()
        mock_buy_rate.return_value = create_mock_bought_shipment()

        # Products in this suite do carry a weight, so zero them to reproduce
        # the real catalogue, where nothing is weighed.
        self.product.weight = 0.0

        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order, weight=None)

        with self.assertRaises(UserError) as caught:
            picking._action_done()

        self.assertIn(picking.name, str(caught.exception))
        mock_buy_rate.assert_not_called()


class TestShippoTransientRateShortfall(ShippoTestBaseCase):
    """Shippo's rate list is not stable between identical calls.

    Observed live on 2026-10-01: one Sacramento to Austin call returned USPS
    only, five identical calls minutes later all carried the full UPS set.
    Pinning a service turns that blip into a refused label on a paid order.
    """

    SECOND_DAY = "ups_second_day_air"

    def setUp(self):
        super().setUp()
        self.carrier.shippo_service_level_token = self.SECOND_DAY
        # Keep the suite fast. The retry policy is what is under test, not how
        # long it waits between attempts.
        self.patcher = patch.object(type(self.carrier), "_SHIPPO_RATE_BACKOFF_SECONDS", 0)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    @staticmethod
    def _without_ups(response):
        trimmed = dict(response)
        trimmed["rates"] = [
            r
            for r in response["rates"]
            if not (r.get("servicelevel") or {}).get("token", "").startswith("ups_")
        ]
        return trimmed

    @patch.object(ShippoRequest, "create_shipment")
    def test_a_single_bad_response_is_retried_not_surfaced(self, mock_create_shipment):
        full = load_shipment_response()
        mock_create_shipment.side_effect = [self._without_ups(full), full]

        order = self._create_sale_order(qty=1)
        res = self.carrier.shippo_rate_shipment(order)

        self.assertTrue(res["success"])
        self.assertEqual(mock_create_shipment.call_count, 2)

    @patch.object(ShippoRequest, "create_shipment")
    def test_each_retry_creates_a_new_shipment(self, mock_create_shipment):
        """The rate list arrives with the shipment, so re-selecting from a
        response that already lacks the service would just fail again."""
        full = load_shipment_response()
        mock_create_shipment.side_effect = [
            self._without_ups(full),
            self._without_ups(full),
            full,
        ]

        order = self._create_sale_order(qty=1)
        self.carrier.shippo_rate_shipment(order)

        self.assertEqual(mock_create_shipment.call_count, 3)

    @patch("requests.get")
    @patch.object(ShippoRequest, "create_shipment")
    @patch.object(ShippoRequest, "buy_rate")
    def test_a_persistent_shortfall_still_refuses(
        self, mock_buy_rate, mock_create_shipment, mock_requests_get
    ):
        """Retrying must not become substituting. If the service is genuinely
        gone, the label is still not bought."""
        mock_requests_get.return_value = mock_requests_get_label()
        mock_buy_rate.return_value = create_mock_bought_shipment()
        mock_create_shipment.return_value = self._without_ups(load_shipment_response())

        sale_order = self._create_sale_order(qty=1)
        picking = self._ready_picking(sale_order)

        with self.assertRaises(UserError) as caught:
            picking._action_done()

        self.assertIn(self.SECOND_DAY, str(caught.exception))
        mock_buy_rate.assert_not_called()
        self.assertEqual(
            mock_create_shipment.call_count,
            self.carrier._SHIPPO_RATE_ATTEMPTS,
            "should have exhausted its attempts before giving up",
        )
