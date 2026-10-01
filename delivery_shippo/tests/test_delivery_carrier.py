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
        picking = sale_order.picking_ids[0]
        picking.action_assign()
        picking.move_line_ids.write({"quantity": 1})

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
        picking = sale_order.picking_ids[0]
        picking.action_assign()
        picking.move_line_ids.write({"quantity": 1})
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
