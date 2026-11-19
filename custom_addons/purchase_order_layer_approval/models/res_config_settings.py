from odoo import fields, models

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    purchase_order_step_ids = fields.One2many(related='company_id.purchase_order_step_ids', readonly=False)
