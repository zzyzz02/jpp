from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')


class TestPurchaseRequest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        group = cls.env.ref('onyx_purchase_request.group_purchase_request_user')
        Users = cls.env['res.users'].with_context(no_reset_password=True)
        cls.users = Users.create([{
            'name': 'PR User %s' % i,
            'login': 'pr_user_%s' % i,
            'email': 'pr_user_%s@example.com' % i,
            'group_ids': [(6, 0, [group.id])],
        } for i in range(3)])
        cls.user1, cls.user2, cls.user3 = cls.users
        cls.manager = Users.create({
            'name': 'PR Manager', 'login': 'pr_manager', 'email': 'pr_manager@example.com',
            'group_ids': [(6, 0, cls.env.ref('onyx_purchase_request.group_purchase_request_manager').ids)],
        })
        cls.company = cls.env.company
        cls.product = cls.env['product.product'].create({
            'name': 'PR Product', 'purchase_ok': True, 'standard_price': 10,
        })
        cls.vendor = cls.env['res.partner'].create({'name': 'PR Vendor'})

    def _request(self, user=None):
        env = self.env(user=user) if user else self.env
        return env['purchase.request'].create({
            'company_id': self.company.id,
            'line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 5})],
        })

    def _steps(self):
        self.company.purchase_request_step_ids.unlink()
        self.env['purchase.request.approval.step'].create([
            {'company_id': self.company.id, 'sequence': 10, 'user_ids': [(6, 0, self.user1.ids)]},
            {'company_id': self.company.id, 'sequence': 20, 'user_ids': [(6, 0, self.user2.ids)]},
        ])

    def test_no_steps_auto_approved(self):
        self.company.purchase_request_step_ids.unlink()
        req = self._request()
        req.action_submit()
        self.assertEqual(req.state, 'approved')

    def test_two_step_approval(self):
        self._steps()
        req = self._request()
        req.action_submit()
        self.assertEqual(req.state, 'submitted')
        with self.assertRaises((UserError, AccessError)):
            req.with_user(self.user3).action_approve()
        with self.assertRaises((UserError, AccessError)):
            req.with_user(self.user2).action_approve()  # not their turn yet
        req.with_user(self.user1).action_approve()
        self.assertEqual(req.state, 'submitted')
        req.with_user(self.user2).action_approve()
        self.assertEqual(req.state, 'approved')

    def test_reject(self):
        self._steps()
        req = self._request()
        req.action_submit()
        req.with_user(self.user1)._do_reject('too expensive')
        self.assertEqual(req.state, 'rejected')

    def test_create_po_from_wizard(self):
        self.company.purchase_request_step_ids.unlink()
        req = self._request()
        req.action_submit()
        line = req.line_ids
        wizard = self.env['purchase.request.po.wizard'].create({
            'partner_id': self.vendor.id, 'line_ids': [(6, 0, line.ids)],
        })
        wizard.action_create()
        po = line.purchase_line_ids.order_id
        self.assertEqual(len(po), 1)
        self.assertEqual(po.picking_type_id, req.picking_type_id)
        self.assertTrue(req.picking_type_id)
        self.assertEqual(line.qty_purchased, 5)
        self.assertEqual(line.process_status, 'processed')

    def test_requester_cannot_force_state(self):
        req = self._request().with_user(self.user1)
        req.sudo().user_id = self.user1
        self.env['purchase.request.approval.step'].create({
            'company_id': self.company.id, 'sequence': 10, 'user_ids': [(6, 0, self.user2.ids)],
        })
        req.action_submit()
        self.assertEqual(req.state, 'submitted')
        with self.assertRaises(AccessError):
            req.write({'state': 'approved'})

    def test_send_from_stock_then_purchase_rest(self):
        self.company.purchase_request_step_ids.unlink()
        req = self._request()
        req.action_submit()
        line = req.line_ids
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.company.id)], limit=1)
        wizard = self.env['purchase.request.stock.wizard'].create({
            'picking_type_id': warehouse.int_type_id.id,
            'line_ids': [(6, 0, line.ids)],
        })
        wizard.location_dest_id = warehouse.lot_stock_id
        wizard.action_create()
        self.assertEqual(line.qty_from_stock, 5)
        self.assertEqual(line.qty_remaining, 0)
        self.assertEqual(line.process_status, 'processed')
        self.assertEqual(req.picking_count, 1)
        with self.assertRaises(UserError):
            self.env['purchase.request.po.wizard'].create({
                'partner_id': self.vendor.id, 'line_ids': [(6, 0, line.ids)],
            }).action_create()

    def test_new_line_without_uom(self):
        line = self.env['purchase.request.line'].new({'quantity': 1.0})
        self.assertEqual(line.process_status, 'not')

    def _one_step(self, users):
        self.company.purchase_request_step_ids.unlink()
        self.env['purchase.request.approval.step'].create({
            'company_id': self.company.id, 'sequence': 10, 'user_ids': [(6, 0, users.ids)],
        })

    def test_copy_resets_state(self):
        req = self._request()
        req.write({'state': 'rejected', 'reject_reason': 'no'})
        req.line_ids.write({'state': 'approved'})
        new = req.copy()
        self.assertEqual(new.state, 'draft')
        self.assertFalse(new.reject_reason)
        self.assertEqual(new.line_ids.state, 'draft')

    def test_user_create_drops_state(self):
        req = self.env['purchase.request'].with_user(self.user1).create({
            'company_id': self.company.id,
            'state': 'approved',
            'line_ids': [(0, 0, {'product_id': self.product.id, 'quantity': 1, 'state': 'approved'})],
        })
        self.assertEqual(req.state, 'draft')
        self.assertEqual(req.line_ids.state, 'draft')
        with self.assertRaises(AccessError):
            req.line_ids.write({'state': 'approved'})

    def test_action_done_manager_only(self):
        self.company.purchase_request_step_ids.unlink()
        req = self._request(self.user1)
        req.action_submit()
        self.assertEqual(req.state, 'approved')
        with self.assertRaises(AccessError):
            req.line_ids.action_done()
        with self.assertRaises(AccessError):
            req.action_done()
        req.with_user(self.manager).action_done()
        self.assertEqual(req.state, 'done')

    def test_action_cancel_rights(self):
        self._one_step(self.user1)
        req = self._request(self.user3)
        req.action_submit()
        with self.assertRaises(AccessError):
            req.with_user(self.user1).action_cancel()
        req.action_cancel()
        self.assertEqual(req.state, 'cancel')

        self.company.purchase_request_step_ids.unlink()
        req = self._request(self.user3)
        req.action_submit()
        self.assertEqual(req.state, 'approved')
        with self.assertRaises(AccessError):
            req.action_cancel()
        req.with_user(self.manager).action_cancel()
        self.assertEqual(req.state, 'cancel')

    def test_action_draft_rights(self):
        self._one_step(self.user1)
        req = self._request(self.user3)
        req.action_submit()
        req.with_user(self.user1)._do_reject('no')
        with self.assertRaises(AccessError):
            req.with_user(self.user1).action_draft()
        req.action_draft()
        self.assertEqual(req.state, 'draft')

    def test_po_wizard_requires_approved_lines(self):
        self._one_step(self.user1)
        req = self._request(self.user3)
        req.action_submit()
        wizard = self.env['purchase.request.po.wizard'].with_user(self.manager).create({
            'partner_id': self.vendor.id, 'line_ids': [(6, 0, req.line_ids.ids)],
        })
        with self.assertRaises(UserError):
            wizard.action_create()

    def test_step_approver_must_be_pr_user(self):
        outsider = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Outsider', 'login': 'pr_outsider',
            'group_ids': [(6, 0, self.env.ref('base.group_user').ids)],
        })
        with self.assertRaises(ValidationError):
            self.env['purchase.request.approval.step'].create({
                'company_id': self.company.id, 'user_ids': [(6, 0, outsider.ids)],
            })

    def test_parallel_approvers_any_one(self):
        self._one_step(self.user1 | self.user2)
        req = self._request(self.user3)
        req.action_submit()
        self.assertEqual(len(req.activity_ids), 2)
        req.with_user(self.user2).action_approve()
        self.assertEqual(req.state, 'approved')
        self.assertEqual(
            req.approval_ids.filtered(lambda a: a.user_id == self.user1).status, 'cancelled')
        self.assertFalse(req.activity_ids)

    def test_can_approve_search(self):
        self._steps()
        req = self._request(self.user3)
        req.action_submit()
        Request = self.env['purchase.request']
        self.assertNotIn(req, Request.with_user(self.user2).search([('can_approve', '=', True)]))
        self.assertIn(req, Request.with_user(self.user1).search([('can_approve', '=', True)]))
        req.with_user(self.user1).action_approve()
        self.assertIn(req, Request.with_user(self.user2).search([('can_approve', '=', True)]))

    def test_picking_type_company_mismatch(self):
        company2 = self.env['res.company'].create({'name': 'PR Company 2'})
        picking_type = self.env['stock.warehouse'].sudo().search([('company_id', '=', company2.id)], limit=1).in_type_id
        self.assertEqual(picking_type.company_id, company2)
        with self.assertRaises(ValidationError):
            self.env['purchase.request'].create({
                'company_id': self.company.id, 'picking_type_id': picking_type.id,
            })
