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
