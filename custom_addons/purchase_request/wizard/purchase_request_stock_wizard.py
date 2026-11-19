from odoo import api, fields, models, Command
from odoo.exceptions import UserError


class PurchaseRequestStockWizard(models.TransientModel):
    _name = 'purchase.request.stock.wizard'
    _description = 'Send Request Lines From Stock'

    line_ids = fields.Many2many('purchase.request.line', string='Request Lines')
    company_id = fields.Many2one('res.company', compute='_compute_company_id')
    picking_type_id = fields.Many2one(
        'stock.picking.type', string='Operation Type', required=True,
        domain="[('code', '=', 'internal'), ('company_id', '=', company_id)]")
    location_id = fields.Many2one(
        'stock.location', string='From', required=True,
        compute='_compute_locations', store=True, readonly=False, precompute=True)
    location_dest_id = fields.Many2one(
        'stock.location', string='To', required=True,
        compute='_compute_locations', store=True, readonly=False, precompute=True)

    @api.depends('line_ids')
    def _compute_company_id(self):
        for wizard in self:
            wizard.company_id = wizard.line_ids.company_id[:1]

    @api.depends('picking_type_id')
    def _compute_locations(self):
        for wizard in self:
            wizard.location_id = wizard.picking_type_id.default_location_src_id
            wizard.location_dest_id = wizard.picking_type_id.default_location_dest_id

    def action_create(self):
        self.ensure_one()
        lines = self.line_ids._get_processable()
        if not lines:
            raise UserError(self.env._('Nothing left to process on the selected lines.'))
        if len(lines.company_id) > 1:
            raise UserError(self.env._('All lines must belong to the same company.'))
        moves = []
        for line in lines:
            vals = {
                'product_id': line.product_id.id,
                'description_picking': line.name or False,
                'product_uom_qty': line.qty_remaining,
                'uom_id': line.uom_id.id,
                'location_id': self.location_id.id,
                'location_dest_id': self.location_dest_id.id,
                'purchase_request_line_ids': [Command.set(line.ids)],
            }
            if line.request_id.date_expected:
                vals['date'] = line.request_id.date_expected
            moves.append(Command.create(vals))
        picking = self.env['stock.picking'].with_company(lines.company_id).create({
            'picking_type_id': self.picking_type_id.id,
            'location_id': self.location_id.id,
            'location_dest_id': self.location_dest_id.id,
            'origin': ', '.join(lines.request_id.mapped('name')),
            'move_ids': moves,
        })
        picking.action_confirm()
        return picking._get_records_action()
