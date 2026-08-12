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
    "installable": True,
    "license": "AGPL-3",
}
