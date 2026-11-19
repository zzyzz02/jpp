from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

ACTIVITY = 'mail.mail_activity_data_todo'
USER_GROUP = 'purchase.group_purchase_user'

class PurchaseOrderApprovalStep(models.Model):
    _name = 'purchase.order.approval.step'
    _description = 'Purchase Order Approval Step'
    _order = 'sequence, id'

    company_id = fields.Many2one('res.company', required=True, ondelete='cascade', default=lambda self: self.env.company)
    sequence = fields.Integer(default=10)
    user_ids = fields.Many2many(
        'res.users', 'purchase_order_step_user_rel', 'step_id', 'user_id',
        string='Approvers', required=True)
    min_amount = fields.Float(string='Min Amount', default=0, help='Amount in company currency.')

    @api.constrains('user_ids')
    def _check_user_ids(self):
        for step in self:
            bad = step.user_ids.filtered(lambda u: not u.has_group(USER_GROUP))
            if bad:
                raise ValidationError(self.env._('Approvers must be Purchase users: %s', ', '.join(bad.mapped('name'))))

class ResCompany(models.Model):
    _inherit = 'res.company'

    purchase_order_step_ids = fields.One2many('purchase.order.approval.step', 'company_id')

class PurchaseOrderApprovalLine(models.Model):
    _name = 'purchase.order.approval.line'
    _description = 'Purchase Order Approval Line'
    _order = 'sequence, id'

    order_id = fields.Many2one('purchase.order', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer()
    user_id = fields.Many2one('res.users', required=True)
    status = fields.Selection(
        [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('cancelled', 'Cancelled')],
        default='pending')
    date = fields.Datetime(string='Action Date')

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    approval_ids = fields.One2many('purchase.order.approval.line', 'order_id', copy=False)
    can_approve = fields.Boolean(compute='_compute_can_approve', search='_search_can_approve')
    reject_reason = fields.Text(readonly=True, copy=False, tracking=True)

    @api.depends('state', 'approval_ids.status', 'approval_ids.user_id', 'approval_ids.sequence')
    def _compute_can_approve(self):
        for rec in self:
            rec.can_approve = rec.state == 'to approve' and self.env.user in rec._current_approvals().user_id

    def _search_can_approve(self, operator, value):
        if operator != 'in':
            return NotImplemented
        candidates = self.search([
            ('state', '=', 'to approve'),
            ('approval_ids', 'any', [('user_id', '=', self.env.uid), ('status', '=', 'pending')]),
        ])
        ids = candidates.filtered(lambda r: self.env.user in r._current_approvals().user_id).ids
        return [('id', 'in', ids)]

    def _layer_steps(self):
        self.ensure_one()
        if self.company_id.po_double_validation != 'two_step':
            return self.env['purchase.order.approval.step']
        amount = self.currency_id._convert(
            self.amount_total, self.company_id.currency_id, self.company_id,
            self.date_order or fields.Date.context_today(self))
        return self.company_id.purchase_order_step_ids.filtered(lambda s: s.min_amount <= amount)

    def _approval_allowed(self):
        self.ensure_one()
        if self.approval_ids:
            statuses = self.approval_ids.mapped('status')
            return 'pending' not in statuses and 'approved' in statuses
        if self.company_id.po_double_validation == 'two_step':
            # layer steps replace core's minimum-amount manager approval
            return not self._layer_steps()
        return super()._approval_allowed()

    def button_confirm(self):
        orders = self.filtered(lambda o: o.state in ('draft', 'sent'))
        orders.approval_ids.sudo().unlink()
        orders.sudo().write({'reject_reason': False})
        res = super().button_confirm()
        for rec in orders.filtered(lambda o: o.state == 'to approve'):
            steps = rec._layer_steps()
            if not steps:
                continue
            self.env['purchase.order.approval.line'].sudo().create([{
                'order_id': rec.id, 'sequence': step.sequence, 'user_id': user.id,
            } for step in steps for user in step.user_ids])
            rec.sudo().write({'locked': True})
            rec._schedule_activities()
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
        if self.state != 'to approve' or not mine:
            raise UserError(self.env._('You cannot act on this purchase order now.'))
        return mine

    def _cancel_pending_approvals(self):
        self.approval_ids.filtered(lambda a: a.status == 'pending').sudo().write({
            'status': 'cancelled', 'date': fields.Datetime.now()})

    def _done_activities(self):
        # sudo: other approvers' activities
        self.sudo().activity_feedback([ACTIVITY])

    def _schedule_activities(self):
        for rec in self:
            for user in rec._current_approvals().user_id:
                rec.sudo().activity_schedule(
                    ACTIVITY, user_id=user.id,
                    summary=self.env._('Approve purchase order %s', rec.name))

    def action_layer_approve(self):
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
                rec.sudo().write({'locked': False})
                rec.sudo().button_approve()

    def action_layer_reject(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Reject'),
            'res_model': 'purchase.order.reject.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_order_id': self.id},
        }

    def _do_layer_reject(self, reason):
        mine = self._my_current_approvals()
        mine.sudo().write({'status': 'rejected', 'date': fields.Datetime.now()})
        self._cancel_pending_approvals()
        self._done_activities()
        self.sudo().write({'state': 'draft', 'locked': False, 'reject_reason': reason})
        self.message_post(body=self.env._('%s rejected: %s', self.env.user.name, reason))

    def button_cancel(self):
        for rec in self.filtered(lambda o: o.state == 'to approve' and 'pending' in o.approval_ids.mapped('status')):
            rec._cancel_pending_approvals()
            rec._done_activities()
            rec.sudo().write({'locked': False})
        return super().button_cancel()

    def button_draft(self):
        self.approval_ids.sudo().unlink()
        return super().button_draft()
