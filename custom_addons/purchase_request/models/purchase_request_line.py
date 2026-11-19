from odoo import api, fields, models, Command
from odoo.exceptions import AccessError, UserError

MANAGER_GROUP = 'purchase_request.group_purchase_request_manager'


class PurchaseRequestLine(models.Model):
    _name = 'purchase.request.line'
    _description = 'Purchase Request Line'
    _order = 'id'

    request_id = fields.Many2one('purchase.request', string='Request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one('res.company', related='request_id.company_id', store=True)
    date = fields.Date(related='request_id.date', store=True)
    user_id = fields.Many2one('res.users', string='Requested By', related='request_id.user_id', store=True)
    picking_type_id = fields.Many2one(related='request_id.picking_type_id', store=True)
    product_id = fields.Many2one('product.product', string='Product', required=True, domain=[('purchase_ok', '=', True)])
    name = fields.Text(string='Description')
    quantity = fields.Float(string='Quantity', default=1.0, digits='Product Unit')
    uom_id = fields.Many2one('uom.uom', string='Unit', compute='_compute_uom_id', store=True, readonly=False)
    price_unit = fields.Float(string='Estimated Price', compute='_compute_price_unit', store=True, readonly=False)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('approved', 'Approved'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], string='Status', default='draft', copy=False)
    purchase_line_ids = fields.Many2many(
        'purchase.order.line', 'purchase_request_line_pol_rel', 'request_line_id', 'purchase_line_id',
        string='Purchase Lines', copy=False)
    move_ids = fields.Many2many(
        'stock.move', 'purchase_request_line_move_rel', 'request_line_id', 'move_id',
        string='Stock Moves', copy=False)
    qty_purchased = fields.Float(string='Purchased', compute='_compute_qty_processed', store=True, digits='Product Unit')
    qty_from_stock = fields.Float(string='From Stock', compute='_compute_qty_processed', store=True, digits='Product Unit')
    qty_remaining = fields.Float(string='Remaining', compute='_compute_qty_processed', store=True, digits='Product Unit')
    process_status = fields.Selection([
        ('not', 'Not Processed'),
        ('partial', 'Partially Processed'),
        ('processed', 'Processed'),
    ], string='Process Status', compute='_compute_qty_processed', store=True)

    @api.depends('product_id')
    def _compute_uom_id(self):
        for line in self:
            line.uom_id = line.product_id.uom_id

    @api.depends('product_id')
    def _compute_price_unit(self):
        for line in self:
            line.price_unit = line.product_id.standard_price

    @api.depends(
        'quantity', 'uom_id',
        'purchase_line_ids.state', 'purchase_line_ids.product_qty', 'purchase_line_ids.uom_id',
        'move_ids.state', 'move_ids.product_uom_qty', 'move_ids.uom_id')
    def _compute_qty_processed(self):
        for line in self:
            line.qty_purchased = sum(
                pol.uom_id._compute_quantity(pol.product_qty, line.uom_id)
                for pol in line.purchase_line_ids if pol.state != 'cancel')
            line.qty_from_stock = sum(
                move.uom_id._compute_quantity(move.product_uom_qty, line.uom_id)
                for move in line.move_ids if move.state != 'cancel')
            done = line.qty_purchased + line.qty_from_stock
            line.qty_remaining = max(line.quantity - done, 0.0)
            if not line.uom_id or line.uom_id.compare(done, 0.0) <= 0:
                line.process_status = 'not'
            elif line.uom_id.compare(done, line.quantity) < 0:
                line.process_status = 'partial'
            else:
                line.process_status = 'processed'

    def _can_set_state(self):
        return self.env.su or self.env.user.has_group(MANAGER_GROUP)

    @api.model_create_multi
    def create(self, vals_list):
        if not self._can_set_state():
            for vals in vals_list:
                vals.pop('state', None)
        return super().create(vals_list)

    def write(self, vals):
        if 'state' in vals and not self._can_set_state():
            raise AccessError(self.env._('Only purchase request managers can change the line status.'))
        return super().write(vals)

    def _check_approved(self):
        if any(line.state != 'approved' for line in self):
            raise UserError(self.env._('Only approved lines can be processed.'))

    def _action_open_wizard(self, res_model, name):
        self._check_approved()
        return {
            'type': 'ir.actions.act_window',
            'name': name,
            'res_model': res_model,
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_line_ids': [Command.set(self.ids)]},
        }

    def _get_processable(self):
        # lock rows so two users can't process the same remaining qty concurrently
        self.lock_for_update(allow_referencing=True)
        self._check_approved()
        return self.filtered(lambda l: l.uom_id.compare(l.qty_remaining, 0.0) > 0)

    def action_create_purchase(self):
        return self._action_open_wizard('purchase.request.po.wizard', self.env._('Create Purchase Order'))

    def action_send_from_stock(self):
        return self._action_open_wizard('purchase.request.stock.wizard', self.env._('Send From Stock'))

    def action_done(self):
        if not self._can_set_state():
            raise AccessError(self.env._('Only purchase request managers can mark lines as done.'))
        self.filtered(lambda l: l.state == 'approved').write({'state': 'done'})
        requests = self.request_id.filtered(
            lambda r: r.state == 'approved'
            and r.line_ids.filtered(lambda l: l.state != 'cancel')
            and all(l.state == 'done' for l in r.line_ids if l.state != 'cancel'))
        requests.write({'state': 'done'})


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    purchase_request_line_ids = fields.Many2many(
        'purchase.request.line', 'purchase_request_line_pol_rel', 'purchase_line_id', 'request_line_id',
        string='Purchase Request Lines', readonly=True, copy=False)


class StockMove(models.Model):
    _inherit = 'stock.move'

    purchase_request_line_ids = fields.Many2many(
        'purchase.request.line', 'purchase_request_line_move_rel', 'move_id', 'request_line_id',
        string='Purchase Request Lines', readonly=True, copy=False)
