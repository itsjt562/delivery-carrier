"""Mock utilities for Shippo API testing.

Fixtures in tests/fixtures/*.json are real responses captured against the
live Shippo sandbox on 2026-08-11 (see delivery_shippo-build-plan-2026-08-11.md),
PII-scrubbed before being committed. Everything here builds on those real
shapes rather than a guessed schema.
"""

import json
from pathlib import Path
from unittest.mock import Mock

from ..models.shippo_request import ShippoShipment

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_shipment_response():
    with open(FIXTURES_DIR / "shipment_response.json", encoding="utf-8") as f:
        return json.load(f)


def load_transaction_response():
    with open(FIXTURES_DIR / "transaction_response.json", encoding="utf-8") as f:
        return json.load(f)


def create_mock_bought_shipment(
    shipment_id=None,
    tracking_code="TRACK123",
    rate=5.17,
    currency="USD",
    carrier_name="USPS",
    carrier_service="Ground Advantage",
    label_url="https://deliver.goshippo.com/example-test-label.pdf",
    tracking_url="https://tools.usps.com/go/TrackConfirmAction_input?origTrackNum=TRACK123",
):
    """Build a ShippoShipment the same way ShippoRequest.buy_rate() would,
    without needing a live rate dict -- mirrors EasyPost tests constructing
    EasyPostShipment directly for mock_buy.return_value.
    """
    txn = load_transaction_response()
    return ShippoShipment(
        shipment_id=shipment_id or txn["object_id"],
        tracking_code=tracking_code,
        label_url=label_url,
        tracking_url=tracking_url,
        rate=rate,
        currency=currency,
        carrier_name=carrier_name,
        carrier_service=carrier_service,
    )


def mock_requests_get_label(content=b"PDF_BINARY_CONTENT"):
    mock_response = Mock()
    mock_response.content = content
    mock_response.status_code = 200
    mock_response.raise_for_status = Mock()
    return mock_response
