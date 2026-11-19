from odoo import api, fields, models, Command
from odoo.exceptions import UserError


class PurchaseRequestPoWizard(models.TransientModel):
    _name = 'purchase.request.po.wizard'
    _description = 'Create Purchase Order from Request Lines'

    partner_id = fields.Many2one('res.partner', string='Vendor', required=True)
    line_ids = fields.Many2many('purchase.request.line', string='Request Lines')
    company_id = fields.Many2one('res.company', compute='_compute_company_id')
    picking_type_id = fields.Many2one(
        'stock.picking.type', string='Deliver To', required=True,
        compute='_compute_picking_type_id', store=True, readonly=False, precompute=True,
        domain="[('code', '=', 'incoming'), ('company_id', '=', company_id)]")

    @api.depends('line_ids')
    def _compute_company_id(self):
        for wizard in self:
            wizard.company_id = wizard.line_ids.company_id[:1]

    @api.depends('line_ids')
    def _compute_picking_type_id(self):
        for wizard in self:
            company = wizard.line_ids.company_id[:1] or self.env.company
            wizard.picking_type_id = (
                wizard.line_ids.picking_type_id[:1]
                or self.env['purchase.order']._get_picking_type(company.id))

    def action_create(self):
        self.ensure_one()
        lines = self.line_ids._get_processable()
        if not lines:
            raise UserError(self.env._('Nothing left to purchase on the selected lines.'))
        if len(lines.company_id) > 1:
            raise UserError(self.env._('All lines must belong to the same company.'))
        order_lines = []
        for line in lines:
            vals = {
                'product_id': line.product_id.id,
                'product_qty': line.qty_remaining,
                'uom_id': line.uom_id.id,
                'price_unit': line.price_unit,
                'purchase_request_line_ids': [Command.set(line.ids)],
            }
            if line.name:
                vals['name'] = line.name
            if line.request_id.date_expected:
                vals['date_planned'] = line.request_id.date_expected
            order_lines.append(Command.create(vals))
        order = self.env['purchase.order'].with_company(lines.company_id).create({
            'partner_id': self.partner_id.id,
            'company_id': lines.company_id.id,
            'picking_type_id': self.picking_type_id.id,
            'origin': ', '.join(lines.request_id.mapped('name')),
            'order_line': order_lines,
        })
        return order._get_records_action()
