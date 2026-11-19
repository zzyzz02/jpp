from odoo import fields, models


class PurchaseRequestRejectWizard(models.TransientModel):
    _name = 'purchase.request.reject.wizard'
    _description = 'Reject Purchase Request'

    request_id = fields.Many2one('purchase.request', string='Request', required=True)
    reason = fields.Text(string='Reason', required=True)

    def action_reject(self):
        self.ensure_one()
        self.request_id._do_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}
