{
    'name': 'Purchase Order Layer Approval',
    'version': '20.0.1.0.0',
    'category': 'Inventory/Purchase',
    'summary': 'Multi-layer approval on purchase orders',
    'depends': ['purchase'],
    'data': [
        'security/ir.access.csv',
        'security/security.xml',
        'wizard/purchase_order_reject_wizard_views.xml',
        'views/purchase_order_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'application': False,
    'license': 'LGPL-3',
}
