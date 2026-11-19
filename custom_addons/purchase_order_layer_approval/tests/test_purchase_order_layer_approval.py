from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestPurchaseOrderLayerApproval(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('purchase.group_purchase_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.user1, cls.user2, cls.user3, cls.requester = Users.create([{
            'name': 'PO Layer %s' % i,
            'login': 'po_layer_%s' % i,
            'email': 'po_layer_%s@example.com' % i,
            'group_ids': [(6, 0, group.ids)],
        } for i in range(4)])
        cls.company = cls.env.company
        cls.company.po_double_validation = 'two_step'
        cls.company.purchase_order_step_ids.unlink()
        cls.product = cls.env['product.product'].create({
            'name': 'PO Layer Product', 'purchase_ok': True, 'standard_price': 10,
        })
        cls.vendor = cls.env['res.partner'].create({'name': 'PO Layer Vendor'})

    def _order(self):
        return self.env['purchase.order'].with_user(self.requester).create({
            'partner_id': self.vendor.id,
            'company_id': self.company.id,
            'order_line': [(0, 0, {
                'product_id': self.product.id, 'product_qty': 5, 'price_unit': 10,
            })],
        })

    def _steps(self, min_amount2=0):
        self.company.purchase_order_step_ids.unlink()
        self.env['purchase.order.approval.step'].create([
            {'company_id': self.company.id, 'sequence': 10, 'user_ids': [(6, 0, self.user1.ids)]},
            {'company_id': self.company.id, 'sequence': 20, 'user_ids': [(6, 0, self.user2.ids)],
             'min_amount': min_amount2},
        ])

    def test_no_steps_goes_to_purchase(self):
        self.company.purchase_order_step_ids.unlink()
        order = self._order()
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertFalse(order.approval_ids)

    def test_two_step_approval(self):
        self._steps()
        order = self._order()
        order.button_confirm()
        self.assertEqual(order.state, 'to approve')
        self.assertTrue(order.locked)
        self.assertEqual(len(order.approval_ids), 2)
        with self.assertRaises((UserError, AccessError)):
            order.with_user(self.user3).action_layer_approve()
        with self.assertRaises((UserError, AccessError)):
            order.with_user(self.user2).action_layer_approve()  # not their turn yet
        order.with_user(self.user1).action_layer_approve()
        self.assertEqual(order.state, 'to approve')
        self.assertTrue(order.locked)
        order.with_user(self.user2).action_layer_approve()
        self.assertEqual(order.state, 'purchase')
        self.assertEqual(order.approval_ids.mapped('status'), ['approved', 'approved'])

    def test_reject_via_method(self):
        self._steps()
        order = self._order()
        order.button_confirm()
        order.with_user(self.user1)._do_layer_reject('too expensive')
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.locked)
        self.assertEqual(order.reject_reason, 'too expensive')
        self.assertEqual(
            order.approval_ids.sorted('sequence').mapped('status'), ['rejected', 'cancelled'])

    def test_reject_via_wizard(self):
        self._steps()
        order = self._order()
        order.button_confirm()
        action = order.with_user(self.user1).action_layer_reject()
        self.assertEqual(action['res_model'], 'purchase.order.reject.wizard')
        wizard = self.env['purchase.order.reject.wizard'].with_user(self.user1).create({
            'order_id': order.id, 'reason': 'wizard reason',
        })
        wizard.action_reject()
        self.assertEqual(order.state, 'draft')
        self.assertFalse(order.locked)
        self.assertEqual(order.reject_reason, 'wizard reason')

    def test_reconfirm_regenerates_lines(self):
        self._steps()
        order = self._order()
        order.button_confirm()
        old_ids = order.approval_ids.ids
        order.with_user(self.user1)._do_layer_reject('redo')
        order.button_confirm()
        self.assertEqual(order.state, 'to approve')
        self.assertTrue(order.locked)
        self.assertFalse(order.reject_reason)
        self.assertEqual(len(order.approval_ids), 2)
        self.assertFalse(set(old_ids) & set(order.approval_ids.ids))
        self.assertEqual(set(order.approval_ids.mapped('status')), {'pending'})

    def test_min_amount_skips_step(self):
        self._steps(min_amount2=1000000)
        order = self._order()
        order.button_confirm()
        self.assertEqual(order.state, 'to approve')
        self.assertEqual(order.approval_ids.user_id, self.user1)
        order.with_user(self.user1).action_layer_approve()
        self.assertEqual(order.state, 'purchase')

    def test_cancel_while_to_approve(self):
        self._steps()
        order = self._order()
        order.button_confirm()
        order.button_cancel()
        self.assertEqual(order.state, 'cancel')
        self.assertFalse(order.locked)
        self.assertEqual(set(order.approval_ids.mapped('status')), {'cancelled'})

    def test_same_step_approvers(self):
        self.company.purchase_order_step_ids.unlink()
        self.env['purchase.order.approval.step'].create({
            'company_id': self.company.id, 'sequence': 10,
            'user_ids': [(6, 0, (self.user1 | self.user2).ids)],
        })
        order = self._order()
        order.button_confirm()
        self.assertEqual(len(order.approval_ids), 2)
        order.with_user(self.user1).action_layer_approve()
        self.assertEqual(order.state, 'purchase')
        by_user = {line.user_id: line.status for line in order.approval_ids}
        self.assertEqual(by_user[self.user1], 'approved')
        self.assertEqual(by_user[self.user2], 'cancelled')

    def test_approval_disabled_ignores_steps(self):
        self._steps()
        self.company.po_double_validation = 'one_step'
        order = self._order()
        order.button_confirm()
        self.assertEqual(order.state, 'purchase')
        self.assertFalse(order.approval_ids)
