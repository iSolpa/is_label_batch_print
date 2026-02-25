{
    'name': 'iS Label Batch Print',
    'version': '19.0.1.0.0',
    'category': 'Inventory',
    'summary': 'Batch product label printing without creating fake stock moves',
    'description': """
Batch product label printing wizard for pre-cut pages.

Features:
- Add arbitrary products and quantities
- Quick-add from recent products, deliveries, and incoming receipts
- Choose available label templates
- Validate missing name/reference/barcode/price with warn or block policies
- Print labels without creating stock moves
    """,
    'author': 'Independent Solutions',
    'license': 'Other proprietary',
    'depends': ['stock', 'product'],
    'data': [
        'security/ir.model.access.csv',
        'views/label_batch_print_wizard_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
