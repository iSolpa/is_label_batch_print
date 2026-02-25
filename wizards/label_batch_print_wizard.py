from collections import defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class LabelBatchPrintWizard(models.TransientModel):
    _name = 'label.batch.print.wizard'
    _description = 'Label Batch Print Wizard'

    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        required=True,
        default=lambda self: self._default_warehouse_id(),
    )
    template_report_id = fields.Many2one(
        'ir.actions.report',
        string='Label Template',
        required=True,
        domain="[(\"report_type\", \"in\", [\"qweb-pdf\", \"qweb-html\"]), (\"model\", \"in\", [\"product.product\", \"product.template\", \"product.label.layout\"]) ]",
    )
    labels_per_page = fields.Integer(string='Labels per Page', required=True, default=20)

    recent_added_days = fields.Integer(string='Recent Added (days)', default=30, required=True)
    recent_delivered_days = fields.Integer(string='Recent Received (days)', default=14, required=True)
    incoming_days = fields.Integer(string='Incoming Soon (days)', default=14, required=True)

    policy_selection = [
        ('ignore', 'Ignore'),
        ('warn', 'Warn'),
        ('block', 'Block'),
    ]
    name_policy = fields.Selection(policy_selection, default='block', required=True)
    default_code_policy = fields.Selection(policy_selection, default='warn', required=True)
    barcode_policy = fields.Selection(policy_selection, default='block', required=True)
    price_policy = fields.Selection(policy_selection, default='block', required=True)

    price_field = fields.Selection(
        [
            ('list_price', 'Sales Price'),
            ('msrp', 'Etiqueta Blanca (MSRP)'),
        ],
        string='Price Field',
        default='list_price',
        required=True,
    )

    line_ids = fields.One2many('label.batch.print.wizard.line', 'wizard_id', string='Products')

    total_labels = fields.Integer(compute='_compute_totals', string='Total Labels', store=False)
    page_count = fields.Integer(compute='_compute_totals', string='Estimated Pages', store=False)
    page_remainder = fields.Integer(compute='_compute_totals', string='Remaining Slots', store=False)

    @api.model
    def _default_warehouse_id(self):
        company = self.env.company
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
        return warehouse.id

    @api.model
    def default_get(self, fields_list):
        vals = super().default_get(fields_list)
        active_model = self.env.context.get('active_model')
        active_ids = self.env.context.get('active_ids', [])

        if 'line_ids' in fields_list and active_ids and active_model in ('product.product', 'product.template'):
            product_ids = self._resolve_products_from_active(active_model, active_ids)
            vals['line_ids'] = [(0, 0, {'product_id': product.id, 'quantity': 1}) for product in product_ids]

        return vals

    def _resolve_products_from_active(self, active_model, active_ids):
        if active_model == 'product.product':
            return self.env['product.product'].browse(active_ids).exists()
        templates = self.env['product.template'].browse(active_ids).exists()
        return templates.mapped('product_variant_id').exists()

    @api.depends('line_ids.quantity', 'labels_per_page')
    def _compute_totals(self):
        for wizard in self:
            total = sum(wizard.line_ids.mapped('quantity'))
            wizard.total_labels = total
            labels_per_page = max(wizard.labels_per_page, 1)
            wizard.page_count = (total // labels_per_page) + (1 if total % labels_per_page else 0)
            wizard.page_remainder = (labels_per_page - (total % labels_per_page)) % labels_per_page

    @api.onchange('template_report_id')
    def _onchange_template_report_id(self):
        if not self.template_report_id:
            return
        report_name = (self.template_report_id.report_name or '').lower()
        if 'msrp' in report_name or 'etiqueta' in report_name or 'blanca' in report_name:
            self.price_field = 'msrp'
        else:
            self.price_field = 'list_price'

    def _ensure_positive_value(self, value, label):
        self.ensure_one()
        if value <= 0:
            raise UserError(_('%s must be greater than zero.') % label)

    def _ensure_labels_per_page(self):
        self.ensure_one()
        self._ensure_positive_value(self.labels_per_page, _('Labels per Page'))

    def _merge_products_into_lines(self, products):
        self.ensure_one()
        if not products:
            return

        existing = {line.product_id.id: line for line in self.line_ids}
        for product in products:
            line = existing.get(product.id)
            if line:
                line.quantity += 1
            else:
                self.env['label.batch.print.wizard.line'].create({
                    'wizard_id': self.id,
                    'product_id': product.id,
                    'quantity': 1,
                })

    def action_add_recent_added(self):
        self.ensure_one()
        self._ensure_positive_value(self.recent_added_days, _('Recent Added (days)'))

        cutoff = fields.Datetime.now() - timedelta(days=self.recent_added_days)
        company = self.env.company
        domain = [
            ('active', '=', True),
            ('create_date', '>=', cutoff),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ]
        products = self.env['product.product'].search(domain)
        self._merge_products_into_lines(products)
        return self._reload_action()

    def action_add_recent_received(self):
        self.ensure_one()
        self._ensure_positive_value(self.recent_delivered_days, _('Recent Received (days)'))

        cutoff = fields.Datetime.now() - timedelta(days=self.recent_delivered_days)
        move_domain = [
            ('state', '=', 'done'),
            ('date', '>=', cutoff),
            ('picking_id.picking_type_id.code', '=', 'incoming'),
            ('picking_id.picking_type_id.warehouse_id', '=', self.warehouse_id.id),
        ]
        moves = self.env['stock.move'].search(move_domain)
        products = moves.mapped('product_id').filtered(lambda p: p.active)
        self._merge_products_into_lines(products)
        return self._reload_action()

    # Backward-compatible alias for older XML/button names.
    def action_add_recent_delivered(self):
        return self.action_add_recent_received()

    def action_add_incoming_soon(self):
        self.ensure_one()
        self._ensure_positive_value(self.incoming_days, _('Incoming Soon (days)'))

        date_from = fields.Datetime.now()
        date_to = date_from + timedelta(days=self.incoming_days)
        move_domain = [
            ('state', 'not in', ['done', 'cancel']),
            ('picking_id.picking_type_id.code', '=', 'incoming'),
            ('picking_id.picking_type_id.warehouse_id', '=', self.warehouse_id.id),
            ('picking_id.scheduled_date', '>=', date_from),
            ('picking_id.scheduled_date', '<=', date_to),
        ]
        moves = self.env['stock.move'].search(move_domain)
        products = moves.mapped('product_id').filtered(lambda p: p.active)
        self._merge_products_into_lines(products)
        return self._reload_action()

    def _line_field_issues(self):
        self.ensure_one()
        self.line_ids._compute_missing_fields()

        policy_map = {
            'missing_name': self.name_policy,
            'missing_default_code': self.default_code_policy,
            'missing_barcode': self.barcode_policy,
            'missing_price': self.price_policy,
        }

        labels = {
            'missing_name': _('Name'),
            'missing_default_code': _('Internal Reference'),
            'missing_barcode': _('Barcode'),
            'missing_price': _('Price'),
        }

        issues = defaultdict(lambda: {'warn': [], 'block': []})
        for line in self.line_ids:
            for field_name, policy in policy_map.items():
                if policy == 'ignore':
                    continue
                if getattr(line, field_name):
                    issues[labels[field_name]][policy].append(line.product_id.display_name)

        return issues

    def _format_issue_message(self, issues, title):
        lines = [title]
        for field_label, values in issues.items():
            blocked = sorted(set(values['block']))
            warned = sorted(set(values['warn']))
            if blocked:
                lines.append(_('- %s (blocking): %s') % (field_label, ', '.join(blocked)))
            if warned:
                lines.append(_('- %s (warning): %s') % (field_label, ', '.join(warned)))
        return '\n'.join(lines)

    def action_validate(self):
        self.ensure_one()
        self._ensure_labels_per_page()
        if not self.line_ids:
            raise UserError(_('Add at least one product line before validating.'))

        issues = self._line_field_issues()
        blocking = any(v['block'] for v in issues.values())

        if blocking:
            raise UserError(self._format_issue_message(issues, _('Label validation failed.')))

        warning_lines = []
        for field_label, values in issues.items():
            warned = sorted(set(values['warn']))
            if warned:
                warning_lines.append(_('%s: %s') % (field_label, ', '.join(warned)))

        if self.page_remainder:
            warning_lines.append(
                _('Current page is partially filled: %s empty slot(s) left (page size: %s).')
                % (self.page_remainder, self.labels_per_page)
            )

        if warning_lines:
            message = '\n'.join(warning_lines)
            return self._notification_action(
                notif_type='warning',
                title=_('Validation warnings'),
                message=message,
                next_action=self._reload_action(),
            )

        return self._notification_action(
            notif_type='success',
            title=_('Validation successful'),
            message=_('All selected lines are valid for printing.'),
            next_action=self._reload_action(),
        )

    def _get_line_price(self, line):
        product = line.product_id

        if self.price_field == 'msrp' and hasattr(product, 'msrp'):
            price = product.msrp
            if price:
                return price

        return product.list_price

    def _prepare_payload_rows(self):
        self.ensure_one()
        rows = []
        for line in self.line_ids.sorted(key=lambda l: l.id):
            rows.append({
                'product_id': line.product_id.id,
                'product_tmpl_id': line.product_id.product_tmpl_id.id,
                'qty': line.quantity,
                'name': line.product_id.display_name,
                'default_code': line.product_id.default_code,
                'barcode': line.product_id.barcode,
                'price': self._get_line_price(line),
            })
        return rows

    def _validate_template_compatibility(self):
        self.ensure_one()
        report = self.template_report_id

        if report.report_type not in ('qweb-pdf', 'qweb-html'):
            raise UserError(_('Selected report must be a QWeb PDF/HTML report.'))

        compatible_models = {'product.product', 'product.template', 'product.label.layout'}
        if report.model not in compatible_models:
            raise UserError(
                _('Selected template is not compatible with product labels. Supported models: %s')
                % ', '.join(sorted(compatible_models))
            )

        report_name = (report.report_name or '').lower()
        known = any(token in report_name for token in ('label', 'barcode'))
        if not known:
            return _(
                'Selected template does not look like a label report. '
                'Printing will continue, but verify template compatibility.'
            )
        return False

    def action_print(self):
        self.ensure_one()
        self._ensure_labels_per_page()
        if not self.line_ids:
            raise UserError(_('Add at least one product line before printing.'))

        issues = self._line_field_issues()
        blocking = any(v['block'] for v in issues.values())
        if blocking:
            raise UserError(self._format_issue_message(issues, _('Label validation failed.')))

        warning_msg = self._validate_template_compatibility()

        report = self.template_report_id
        payload = {
            'label_batch_print': {
                'wizard_id': self.id,
                'warehouse_id': self.warehouse_id.id,
                'labels_per_page': self.labels_per_page,
                'total_labels': self.total_labels,
                'page_count': self.page_count,
                'page_remainder': self.page_remainder,
                'warning': warning_msg or '',
                'rows': self._prepare_payload_rows(),
            }
        }

        if report.model == 'product.template':
            docs = self.line_ids.mapped('product_id.product_tmpl_id')
            return report.report_action(docs, data=payload)

        if report.model == 'product.product':
            docs = self.line_ids.mapped('product_id')
            return report.report_action(docs, data=payload)

        # product.label.layout reports are custom and may ignore docids.
        return report.report_action(self, data=payload)

    def _reload_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Batch Label Printing'),
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _notification_action(self, notif_type, title, message, next_action=None):
        params = {
            'type': notif_type,
            'title': title,
            'message': message,
            'sticky': False,
        }
        if next_action:
            params['next'] = next_action
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': params,
        }


class LabelBatchPrintWizardLine(models.TransientModel):
    _name = 'label.batch.print.wizard.line'
    _description = 'Label Batch Print Wizard Line'

    wizard_id = fields.Many2one('label.batch.print.wizard', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', required=True)
    quantity = fields.Integer(default=1, required=True)

    missing_name = fields.Boolean(compute='_compute_missing_fields', store=False)
    missing_default_code = fields.Boolean(compute='_compute_missing_fields', store=False)
    missing_barcode = fields.Boolean(compute='_compute_missing_fields', store=False)
    missing_price = fields.Boolean(compute='_compute_missing_fields', store=False)

    validation_status = fields.Selection(
        [('ok', 'OK'), ('warning', 'Warning'), ('block', 'Block')],
        compute='_compute_validation_feedback',
        store=False,
    )
    validation_message = fields.Char(compute='_compute_validation_feedback', store=False)

    @api.constrains('quantity')
    def _check_quantity(self):
        for line in self:
            if line.quantity <= 0:
                raise UserError(_('Quantity must be greater than zero.'))

    @api.depends('product_id', 'wizard_id.price_field')
    def _compute_missing_fields(self):
        for line in self:
            product = line.product_id
            line.missing_name = not bool(product.name)
            line.missing_default_code = not bool(product.default_code)

            has_barcode = bool(product.barcode)
            if not has_barcode and hasattr(product, 'barcode_ids'):
                has_barcode = bool(product.barcode_ids)
            line.missing_barcode = not has_barcode

            price_field = line.wizard_id.price_field or 'list_price'
            price_value = False
            if price_field == 'msrp' and hasattr(product, 'msrp'):
                price_value = product.msrp
            if not price_value:
                price_value = product.list_price
            line.missing_price = not bool(price_value)

    @api.depends(
        'missing_name',
        'missing_default_code',
        'missing_barcode',
        'missing_price',
        'wizard_id.name_policy',
        'wizard_id.default_code_policy',
        'wizard_id.barcode_policy',
        'wizard_id.price_policy',
    )
    def _compute_validation_feedback(self):
        for line in self:
            checks = [
                (line.missing_name, line.wizard_id.name_policy, _('Name missing')),
                (line.missing_default_code, line.wizard_id.default_code_policy, _('Internal reference missing')),
                (line.missing_barcode, line.wizard_id.barcode_policy, _('Barcode missing')),
                (line.missing_price, line.wizard_id.price_policy, _('Price missing')),
            ]

            messages = []
            status = 'ok'
            for missing, policy, msg in checks:
                if not missing or policy == 'ignore':
                    continue
                messages.append(msg)
                if policy == 'block':
                    status = 'block'
                elif status != 'block':
                    status = 'warning'

            line.validation_status = status
            line.validation_message = ', '.join(messages) if messages else _('Ready to print')
