import logging

import requests

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

BASE_URL = "https://api.goshippo.com"


class ShippoShipment:
    """Normalized result of a bought transaction -- mirrors EasyPostShipment
    in delivery_easypost_oca so delivery_carrier.py can stay carrier-agnostic
    at the call site.
    """

    def __init__(
        self,
        shipment_id,
        tracking_code,
        label_url,
        tracking_url,
        rate,
        currency,
        carrier_name,
        carrier_service,
    ):
        self.shipment_id = shipment_id
        self.tracking_code = tracking_code
        self.label_url = label_url
        self.tracking_url = tracking_url
        self.rate = rate
        self.currency = currency
        self.carrier_name = carrier_name
        self.carrier_service = carrier_service

    def get_label_content(self):
        try:
            response = requests.get(self.label_url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as e:
            _logger.error("Failed to retrieve Shippo label content: %s", e)
            raise UserError(_("Failed to retrieve label content.")) from e
        return response.content


class ShippoRequest:
    def __init__(self, carrier):
        self.carrier = carrier
        self.api_key = self._get_api_key()

    def _get_api_key(self):
        if self.carrier.prod_environment:
            return self.carrier.shippo_production_api_key
        return self.carrier.shippo_test_api_key

    def _headers(self):
        return {
            "Authorization": f"ShippoToken {self.api_key}",
            "Content-Type": "application/json",
        }

    def _call(self, method, path, json=None):
        try:
            response = requests.request(
                method, f"{BASE_URL}{path}", headers=self._headers(), json=json, timeout=30
            )
        except requests.RequestException as e:
            _logger.error("Shippo request failed: %s", e)
            raise UserError(_("Shippo request failed: %s") % e) from e
        if response.status_code >= 400:
            _logger.error("Shippo API error %s: %s", response.status_code, response.text)
            raise UserError(
                _("Shippo API error (%s): %s") % (response.status_code, response.text)
            )
        return response.json()

    # -- Address validation ------------------------------------------------
    # NOTE (probe finding, 2026-08-11): address_from without phone+email is
    # NOT rejected by /shipments/ -- it silently drops carriers from the rate
    # list (UPS test rates vanished entirely) and later hard-fails at
    # /transactions with "sender_info_missing" if the surviving USPS rate is
    # bought. Always populate phone+email on address_from before calling
    # create_shipment, or rate shopping quietly narrows with no error raised.

    def create_shipment(self, from_address: dict, to_address: dict, parcel: dict):
        """POST /shipments/ -- returns the raw shipment dict, including
        a `rates` list. Caller picks a rate (e.g. lowest by `amount`).
        """
        body = {
            "address_from": from_address,
            "address_to": to_address,
            "parcels": [parcel],
            "async": False,
        }
        data = self._call("POST", "/shipments/", json=body)
        if data.get("status") != "SUCCESS":
            raise UserError(
                _("Shippo shipment creation did not succeed: %s") % data.get("messages")
            )
        return data

    def lowest_rate(self, shipment: dict) -> dict:
        rates = shipment.get("rates", [])
        if not rates:
            raise UserError(_("No rate found for this shipping."))
        return min(rates, key=lambda r: float(r["amount"]))

    def rate_for_service(self, shipment: dict, token: str) -> dict:
        """Pick the rate for one named service level, or refuse.

        WHY THIS EXISTS RATHER THAN JUST SORTING DIFFERENTLY

        A storefront that sells a named tier at a flat price has already made a
        promise about transit time. Buying the cheapest rate instead breaks that
        promise silently, and the first person to find out is the customer who
        paid for 2-day. Measured against the real rate list from a Sacramento
        origin on 2026-10-01, `lowest_rate` on a lower-48 shipment returns
        `ups_ground_saver` at a 4-day estimate, so a $20 2-day order would ship
        as a 4-day parcel with nothing anywhere recording that it had happened.

        WHY IT RAISES INSTEAD OF FALLING BACK

        A substitution is the bug. If the configured service is genuinely not
        available for a destination, the honest outcomes are a different tier or
        a human decision, not a quiet downgrade. `_send_confirmation_email`
        turns this UserError into a scheduled warning activity on the picking,
        so it surfaces as work to do rather than a lost order.
        """
        rates = shipment.get("rates", [])
        if not rates:
            raise UserError(_("No rate found for this shipping."))
        for rate in rates:
            if (rate.get("servicelevel") or {}).get("token") == token:
                return rate
        offered = sorted(
            {
                (r.get("servicelevel") or {}).get("token")
                for r in rates
                if (r.get("servicelevel") or {}).get("token")
            }
        )
        raise UserError(
            _(
                "Shippo did not offer the service this delivery method sells, so "
                "no label was bought.\n\n"
                "Required service level: %(token)s\n"
                "Offered for this shipment: %(offered)s\n\n"
                "Either this destination cannot take that service, or the carrier "
                "account no longer has it enabled."
            )
            % {"token": token, "offered": ", ".join(offered) or _("none")}
        )

    def buy_rate(self, rate: dict, label_file_type: str = "PDF") -> "ShippoShipment":
        """POST /transactions -- purchases the label for a given rate object."""
        body = {
            "rate": rate["object_id"],
            "label_file_type": label_file_type,
            "async": False,
        }
        data = self._call("POST", "/transactions", json=body)
        if data.get("status") != "SUCCESS":
            raise UserError(
                _("Shippo label purchase failed: %s") % data.get("messages")
            )
        return ShippoShipment(
            shipment_id=data.get("object_id"),
            tracking_code=data.get("tracking_number"),
            label_url=data.get("label_url"),
            tracking_url=data.get("tracking_url_provider"),
            rate=float(rate["amount"]),
            currency=rate.get("currency", "USD"),
            carrier_name=rate.get("provider"),
            carrier_service=rate.get("servicelevel", {}).get("name"),
        )

    def refund_transaction(self, transaction_id: str) -> dict:
        """POST /refunds -- void/refund a purchased label. Shippo supports
        this natively; EasyPost's OCA module does not implement cancel at
        all (easypost_oca_cancel_shipment just raises UserError). Real
        capability gap we can close by porting this.
        """
        body = {"transaction": transaction_id}
        return self._call("POST", "/refunds", json=body)

    def get_tracking_status(self, carrier: str, tracking_number: str) -> dict:
        return self._call("GET", f"/tracks/{carrier}/{tracking_number}")
