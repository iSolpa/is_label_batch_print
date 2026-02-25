from odoo.tests.common import TransactionCase


class TestLabelBatchPrint(TransactionCase):

    def setUp(self):
        super().setUp()
        self.warehouse = self.env['stock.warehouse'].search([], limit=1)
        self.product = self.env['product.product'].create({
            'name': 'Label Test Product',
            'default_code': 'LBL-001',
            'barcode': '1234567890123',
            'list_price': 9.99,
            'type': 'consu',
        })

        self.report = self.env['ir.actions.report'].search([
            ('report_type', 'in', ('qweb-pdf', 'qweb-html')),
            ('model', 'in', ('product.product', 'product.template')),
        ], limit=1)

    def test_totals_and_remainder(self):
        wizard = self.env['label.batch.print.wizard'].create({
            'warehouse_id': self.warehouse.id,
            'template_report_id': self.report.id,
            'labels_per_page': 20,
            'line_ids': [
                (0, 0, {'product_id': self.product.id, 'quantity': 23}),
            ],
        })
        wizard._compute_totals()
        self.assertEqual(wizard.total_labels, 23)
        self.assertEqual(wizard.page_count, 2)
        self.assertEqual(wizard.page_remainder, 3)

    def test_merge_product_quantities(self):
        wizard = self.env['label.batch.print.wizard'].create({
            'warehouse_id': self.warehouse.id,
            'template_report_id': self.report.id,
            'labels_per_page': 20,
        })

        wizard._merge_products_into_lines(self.product)
        wizard._merge_products_into_lines(self.product)

        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(wizard.line_ids.quantity, 2)

    def test_missing_barcode_detected(self):
        product = self.env['product.product'].create({
            'name': 'No Barcode Product',
            'default_code': 'LBL-002',
            'list_price': 5.00,
            'type': 'consu',
        })
        wizard = self.env['label.batch.print.wizard'].create({
            'warehouse_id': self.warehouse.id,
            'template_report_id': self.report.id,
            'line_ids': [(0, 0, {'product_id': product.id, 'quantity': 1})],
            'barcode_policy': 'block',
        })
        line = wizard.line_ids
        line._compute_missing_fields()
        self.assertTrue(line.missing_barcode)
