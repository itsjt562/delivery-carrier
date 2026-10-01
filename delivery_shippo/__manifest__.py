{
    "name": "Shippo Shipping OCA",
    "version": "18.0.1.3.0",
    "summary": "Shippo pinned service levels, declared parcel size and weight, labels, tracking, void",
    "author": "Helm Compounds",
    "website": "https://github.com/itsjt562/delivery-carrier",
    "category": "Inventory/Delivery",
    "depends": [
        "stock_delivery",
        "mail",
    ],
    "data": [
        "views/delivery_carrier_views.xml",
        "views/stock_picking_views.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "license": "AGPL-3",
}
