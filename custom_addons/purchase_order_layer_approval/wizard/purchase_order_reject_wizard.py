from odoo import fields, models

class PurchaseOrderRejectWizard(models.TransientModel):
    _name = 'purchase.order.reject.wizard'
    _description = 'Reject Purchase Order'

    order_id = fields.Many2one('purchase.order', string='Purchase Order', required=True)
    reason = fields.Text(string='Reason', required=True)

    def action_reject(self):
        self.ensure_one()
        self.order_id._do_layer_reject(self.reason)
        return {'type': 'ir.actions.act_window_close'}
