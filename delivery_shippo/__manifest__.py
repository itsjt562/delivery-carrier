{
    "name": "Shippo Shipping OCA",
    "version": "18.0.1.0.0",
    "summary": "Shippo multi-carrier rate shopping, label purchase, tracking, void",
    "author": "Helm Compounds",
    "website": "https://github.com/itsjt562/delivery-carrier",
    "category": "Inventory/Delivery",
    "depends": [
        "stock_delivery",
        "mail",
    ],
    "data": [
        "views/delivery_carrier_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    # Flip to True once models/shippo_request.py and delivery_carrier.py have
    # real logic. Skeleton only as of 2026-08-11 -- see build plan doc.
    "installable": False,
    "license": "AGPL-3",
}
