from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

ACTIVITY = 'mail.mail_activity_data_todo'
MANAGER_GROUP = 'purchase_request.group_purchase_request_manager'
USER_GROUP = 'purchase_request.group_purchase_request_user'


class PurchaseRequestApprovalStep(models.Model):
    _name = 'purchase.request.approval.step'
    _description = 'Purchase Request Approval Step'
    _order = 'sequence, id'

    company_id = fields.Many2one('res.company', required=True, ondelete='cascade', default=lambda self: self.env.company)
    sequence = fields.Integer(default=10)
    user_ids = fields.Many2many(
        'res.users', 'purchase_request_step_user_rel', 'step_id', 'user_id',
        string='Approvers', required=True)
    min_amount = fields.Float(string='Min Amount', default=0)

    @api.constrains('user_ids')
    def _check_user_ids(self):
        for step in self:
            bad = step.user_ids.filtered(lambda u: not u.has_group(USER_GROUP))
            if bad:
                raise ValidationError(self.env._('Approvers must be Purchase Request users: %s', ', '.join(bad.mapped('name'))))


class ResCompany(models.Model):
    _inherit = 'res.company'

    purchase_request_step_ids = fields.One2many('purchase.request.approval.step', 'company_id')


class PurchaseRequestApprovalLine(models.Model):
    _name = 'purchase.request.approval.line'
    _description = 'Purchase Request Approval Line'
    _order = 'sequence, id'

    request_id = fields.Many2one('purchase.request', required=True, ondelete='cascade')
    sequence = fields.Integer()
    user_id = fields.Many2one('res.users', required=True)
    status = fields.Selection(
        [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='pending')
    date = fields.Datetime(string='Action Date')


class PurchaseRequest(models.Model):
    _name = 'purchase.request'
    _description = 'Purchase Request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'
    _mail_post_access = 'read'

    name = fields.Char(string='Request No', default='New', readonly=True, copy=False)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    user_id = fields.Many2one('res.users', string='Requested By', default=lambda self: self.env.user, required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date_expected = fields.Datetime(string='Expected Arrival')
    picking_type_id = fields.Many2one(
        'stock.picking.type', string='Deliver To', tracking=True,
        default=lambda self: self.env['purchase.order']._get_picking_type(self.env.company.id),
        domain="[('code', '=', 'incoming'), ('company_id', '=', company_id)]")
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'), ('approved', 'Approved'),
         ('done', 'Done'), ('rejected', 'Rejected'), ('cancel', 'Cancelled')],
        default='draft', tracking=True, copy=False)
    line_ids = fields.One2many('purchase.request.line', 'request_id', copy=True)
    approval_ids = fields.One2many('purchase.request.approval.line', 'request_id')
    amount_total = fields.Float(compute='_compute_amount_total', store=True)
    can_approve = fields.Boolean(compute='_compute_can_approve', search='_search_can_approve')
    purchase_count = fields.Integer(compute='_compute_purchase_count')
    picking_count = fields.Integer(compute='_compute_purchase_count')
    reject_reason = fields.Text(readonly=True, tracking=True, copy=False)

    @api.depends('line_ids.quantity', 'line_ids.price_unit')
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(l.quantity * l.price_unit for l in rec.line_ids)

    @api.depends('state', 'approval_ids.status', 'approval_ids.user_id', 'approval_ids.sequence')
    def _compute_can_approve(self):
        for rec in self:
            rec.can_approve = rec.state == 'submitted' and self.env.user in rec._current_approvals().user_id

    def _search_can_approve(self, operator, value):
        if operator != 'in':
            return NotImplemented
        candidates = self.search([
            ('state', '=', 'submitted'),
            ('approval_ids', 'any', [('user_id', '=', self.env.uid), ('status', '=', 'pending')]),
        ])
        ids = candidates.filtered(lambda r: self.env.user in r._current_approvals().user_id).ids
        return [('id', 'in', ids)]

    def _compute_purchase_count(self):
        for rec in self:
            lines = rec.sudo().line_ids
            rec.purchase_count = len(lines.purchase_line_ids.order_id)
            rec.picking_count = len(lines.move_ids.picking_id)

    @api.constrains('company_id', 'picking_type_id')
    def _check_picking_type_company(self):
        for rec in self:
            if rec.picking_type_id.company_id and rec.picking_type_id.company_id != rec.company_id:
                raise ValidationError(self.env._('The receipt operation type must belong to the request company.'))

    @api.onchange('company_id')
    def _onchange_company_id(self):
        self.picking_type_id = self.env['purchase.order']._get_picking_type(self.company_id.id)

    def _is_manager(self):
        return self.env.su or self.env.user.has_group(MANAGER_GROUP)

    def _check_manager(self):
        if not self._is_manager():
            raise AccessError(self.env._('Only purchase request managers can do this.'))

    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_manager():
            for vals in vals_list:
                vals.pop('state', None)
                vals.pop('reject_reason', None)
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].next_by_code('purchase.request') or 'New'
        return super().create(vals_list)

    def write(self, vals):
        if 'state' in vals and not self._is_manager():
            raise AccessError(self.env._('Status can only change through workflow buttons.'))
        res = super().write(vals)
        if 'state' in vals:
            state = vals['state']
            skip = ('done',) if state == 'draft' else ('done', 'cancel')
            if state == 'rejected':
                state = 'draft'
            self.sudo().line_ids.filtered(lambda l: l.state not in skip).write({'state': state})
        return res

    def _current_approvals(self):
        self.ensure_one()
        pending = self.approval_ids.filtered(lambda a: a.status == 'pending')
        if not pending:
            return pending
        seq = min(pending.mapped('sequence'))
        return pending.filtered(lambda a: a.sequence == seq)

    def _my_current_approvals(self):
        self.ensure_one()
        mine = self._current_approvals().filtered(lambda a: a.user_id == self.env.user)
        if self.state != 'submitted' or not mine:
            raise UserError(self.env._('You cannot act on this request now.'))
        return mine

    def _cancel_pending_approvals(self):
        self.approval_ids.filtered(lambda a: a.status == 'pending').sudo().write({
            'status': 'cancelled', 'date': fields.Datetime.now()})

    def _done_activities(self):
        # sudo: other approvers' activities and requester cancelling
        self.sudo().activity_feedback([ACTIVITY])

    def _schedule_activities(self):
        for rec in self:
            for user in rec._current_approvals().user_id:
                rec.sudo().activity_schedule(
                    ACTIVITY, user_id=user.id,
                    summary=self.env._('Approve purchase request %s', rec.name))

    def action_submit(self):
        auto = submitted = self.browse()
        line_vals = []
        for rec in self:
            if rec.state != 'draft':
                raise UserError(self.env._('Only draft requests can be submitted.'))
            if not rec.line_ids:
                raise UserError(self.env._('Add at least one product line.'))
            steps = rec.company_id.purchase_request_step_ids.filtered(lambda s: s.min_amount <= rec.amount_total)
            if steps:
                submitted |= rec
                line_vals += [{
                    'request_id': rec.id, 'sequence': step.sequence, 'user_id': user.id, 'status': 'pending',
                } for step in steps for user in step.user_ids]
            else:
                auto |= rec
        self.approval_ids.sudo().unlink()
        self.env['purchase.request.approval.line'].sudo().create(line_vals)
        if auto:
            auto.sudo().write({'state': 'approved'})
            for rec in auto:
                rec.message_post(body=self.env._('Approved automatically (no approval step).'))
        if submitted:
            submitted.sudo().write({'state': 'submitted'})
            submitted._schedule_activities()

    def action_approve(self):
        approved = self.browse()
        for rec in self:
            current = rec._current_approvals()
            mine = rec._my_current_approvals()
            now = fields.Datetime.now()
            mine.sudo().write({'status': 'approved', 'date': now})
            (current - mine).sudo().write({'status': 'cancelled', 'date': now})
            rec._done_activities()
            rec.message_post(body=self.env._('%s approved.', self.env.user.name))
            if rec._current_approvals():
                rec._schedule_activities()
            else:
                approved |= rec
        if approved:
            approved.sudo().write({'state': 'approved'})

    def action_reject(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Reject'),
            'res_model': 'purchase.request.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_request_id': self.id},
        }

    def _do_reject(self, reason):
        mine = self._my_current_approvals()
        mine.sudo().write({'status': 'rejected', 'date': fields.Datetime.now()})
        self._cancel_pending_approvals()
        self._done_activities()
        self.sudo().write({'state': 'rejected', 'reject_reason': reason})
        self.message_post(body=self.env._('%s rejected: %s', self.env.user.name, reason))

    def action_cancel(self):
        is_manager = self._is_manager()
        for rec in self:
            if not is_manager and not (rec.user_id == self.env.user and rec.state in ('draft', 'submitted')):
                raise AccessError(self.env._('You cannot cancel this request.'))
            if rec.state == 'done':
                raise UserError(self.env._('Done requests cannot be cancelled.'))
            lines = rec.sudo().line_ids
            if lines.purchase_line_ids.filtered(lambda l: l.state != 'cancel'):
                raise UserError(self.env._('Cancel the linked purchase orders first.'))
            if lines.move_ids.filtered(lambda m: m.state != 'cancel'):
                raise UserError(self.env._('Cancel the linked transfers first.'))
        self._cancel_pending_approvals()
        self._done_activities()
        self.sudo().write({'state': 'cancel'})

    def action_draft(self):
        is_manager = self._is_manager()
        for rec in self:
            if not is_manager and rec.user_id != self.env.user:
                raise AccessError(self.env._('Only the requester or a manager can reset this request.'))
            if rec.state not in ('rejected', 'cancel'):
                raise UserError(self.env._('Only rejected or cancelled requests can be reset to draft.'))
        self.approval_ids.sudo().unlink()
        self.sudo().write({'state': 'draft'})

    def action_done(self):
        self._check_manager()
        if any(rec.state != 'approved' for rec in self):
            raise UserError(self.env._('Only approved requests can be marked done.'))
        self.sudo().write({'state': 'done'})

    def _action_view_records(self, xmlid, records):
        action = self.env['ir.actions.actions']._for_xml_id(xmlid)
        action['domain'] = [('id', 'in', records.ids)]
        action['context'] = {}
        if len(records) == 1:
            action['views'] = [(False, 'form')]
            action['res_id'] = records.id
        return action

    def action_view_purchase(self):
        self.ensure_one()
        return self._action_view_records('purchase.purchase_rfq', self.line_ids.purchase_line_ids.order_id)

    def action_view_picking(self):
        self.ensure_one()
        return self._action_view_records('stock.action_picking_tree_all', self.line_ids.move_ids.picking_id)
