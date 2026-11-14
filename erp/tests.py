from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from .models import Site, SiteAccess, Product, Partner, Order, OrderLine, StockBalance, StockMovement, Production, QualityTest, AuditEvent
from .services import move_stock, transition_order, transition_production

@override_settings(EMAIL_OTP_ENABLED=False)
class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maker = User.objects.create_user('maker', password='Test-Maker-327!')
        cls.checker = User.objects.create_user('checker', password='Test-Checker-327!')
        cls.admin = User.objects.create_superuser('root', 'root@example.test', 'Test-Admin-327!')
        permissions = Permission.objects.filter(content_type__app_label='erp')
        cls.maker.user_permissions.set(permissions)
        cls.checker.user_permissions.set(permissions)
        cls.site = Site.objects.create(code='S1', name='Plant One', kind='Plant')
        cls.other = Site.objects.create(code='S2', name='Other Plant', kind='Plant')
        for user in [cls.maker, cls.checker]: SiteAccess.objects.create(user=user, site=cls.site)
        cls.product = Product.objects.create(code='C1', name='Cement', category='Cement', unit='MT')
        cls.product2 = Product.objects.create(code='C2', name='Cement Two', category='Cement', unit='MT')
        cls.dealer = Partner.objects.create(code='D1', name='Test Dealer', kind='Dealer', credit_limit=10000)
        cls.supplier = Partner.objects.create(code='V1', name='Supplier', kind='Supplier')

    def make_order(self, quantity=10, rate=100, kind='Sale', site=None):
        order = Order.objects.create(site=site or self.site, kind=kind, partner=self.dealer if kind == 'Sale' else self.supplier, created_by=self.maker)
        OrderLine.objects.create(order=order, product=self.product, quantity=quantity, rate=rate)
        return order

    def approve(self, order):
        transition_order(self.maker, order.pk, 'submit')
        transition_order(self.checker, order.pk, 'approve', backorder_reason='Partner supply due')

    def opening(self, product=None, quantity=100):
        return move_stock(self.admin, self.site, product or self.product, Decimal(quantity), 'opening:' + str((product or self.product).pk), 'Opening')

    def test_self_approval_blocked(self):
        order = self.make_order()
        transition_order(self.maker, order.pk, 'submit')
        with self.assertRaises(ValidationError): transition_order(self.maker, order.pk, 'approve')
        order.refresh_from_db()
        self.assertEqual(order.status, 'Submitted')

    def test_superuser_cannot_self_approve(self):
        order = self.make_order()
        order.created_by = self.admin
        order.save()
        transition_order(self.admin, order.pk, 'submit')
        with self.assertRaises(ValidationError): transition_order(self.admin, order.pk, 'approve')

    def test_full_sales_workflow_posts_once(self):
        self.opening()
        order = self.make_order()
        self.approve(order)
        transition_order(self.checker, order.pk, 'post')
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.product).quantity, 90)
        with self.assertRaises(ValidationError): transition_order(self.checker, order.pk, 'post')
        self.assertEqual(StockMovement.objects.filter(kind='Dispatch').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(reference=order.number).count(), 3)

    def test_insufficient_stock_rolls_back_all_lines(self):
        self.opening()
        order = self.make_order()
        OrderLine.objects.create(order=order, product=self.product2, quantity=100, rate=1)
        self.approve(order)
        with self.assertRaises(ValidationError): transition_order(self.checker, order.pk, 'post')
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.product).quantity, 100)
        self.assertFalse(StockMovement.objects.filter(kind='Dispatch').exists())
        order.refresh_from_db()
        self.assertEqual(order.status, 'Approved')

    def receive(self, order):
        from . import inbound
        line = order.lines.get()
        g = inbound.gate_in(self.maker, order, {'vehicle_number': 'MP04AB1234', 'challan_number': f'CH-{order.pk}'}, {line: line.quantity})
        gl = g.lines.get()
        inbound.post(self.checker, g.pk, {gl: {'received': line.quantity, 'accepted': line.quantity, 'rejected': 0, 'damaged': 0, 'hold': 0, 'reason': ''}})

    def test_purchase_receipt_increases_stock(self):
        order = self.make_order(kind='Purchase')
        self.approve(order)
        with self.assertRaisesMessage(ValidationError, 'Goods receipt'):
            transition_order(self.checker, order.pk, 'post')
        self.receive(order)
        self.assertEqual(StockBalance.objects.get(product=self.product, site=self.site).quantity, 10)
        self.assertEqual(Order.objects.get(pk=order.pk).status, 'Completed')

    def test_credit_limit_includes_approved_orders(self):
        self.dealer.credit_limit = 1500
        self.dealer.save()
        self.approve(self.make_order())
        order = self.make_order()
        transition_order(self.maker, order.pk, 'submit', confirm_duplicate=True)
        with self.assertRaises(ValidationError): transition_order(self.checker, order.pk, 'approve')

    def test_completed_order_cannot_cancel(self):
        order = self.make_order(kind='Purchase')
        self.approve(order)
        self.receive(order)
        with self.assertRaises(ValidationError): transition_order(self.maker, order.pk, 'cancel')

    def test_duplicate_source_rolls_back_balance(self):
        self.opening()
        with self.assertRaises(IntegrityError): self.opening()
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.product).quantity, 100)

    def test_permissions_enforced_in_service(self):
        user = User.objects.create_user('viewer')
        SiteAccess.objects.create(user=user, site=self.site)
        with self.assertRaises(PermissionDenied): transition_order(user, self.make_order().pk, 'submit')

    def batch(self):
        return Production.objects.create(site=self.site, product=self.product, created_by=self.maker, planned_quantity=100, actual_quantity=95, batch='B1', shift='A')

    def test_quality_hold_blocks_unchecked_release(self):
        batch = self.batch()
        transition_production(self.maker, batch.pk, 'complete')
        self.assertFalse(StockBalance.objects.exists())
        with self.assertRaises(ValidationError): transition_production(self.checker, batch.pk, 'release')

    def test_failed_test_blocks_release(self):
        batch = self.batch()
        transition_production(self.maker, batch.pk, 'complete')
        QualityTest.objects.create(site=self.site, production=batch, created_by=self.checker, test_name='Strength', result='Fail', disposition='Fail')
        with self.assertRaises(ValidationError): transition_production(self.checker, batch.pk, 'release')

    def test_quality_release_posts_once(self):
        batch = self.batch()
        transition_production(self.maker, batch.pk, 'complete')
        QualityTest.objects.create(site=self.site, production=batch, created_by=self.checker, test_name='Strength', result='Pass', disposition='Pass')
        with self.assertRaises(ValidationError): transition_production(self.maker, batch.pk, 'release')
        transition_production(self.checker, batch.pk, 'release')
        with self.assertRaises(ValidationError): transition_production(self.checker, batch.pk, 'release')
        self.assertEqual(StockBalance.objects.get(product=self.product).quantity, 95)

    def test_pages_render_and_require_login(self):
        routes = ['dashboard', 'sales', 'purchases', 'inventory', 'production', 'quality', 'partners', 'products', 'sites', 'reports', 'audit', 'production_new', 'quality_new', 'opening']
        for route in routes:
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)
        self.client.force_login(self.admin)
        for route in routes:
            with self.subTest(route=route): self.assertEqual(self.client.get(reverse(route)).status_code, 200)
        self.assertEqual(self.client.get(reverse('order_new', args=['Sale'])).status_code, 200)

    def test_site_isolation_for_detail_and_action(self):
        order = self.make_order(site=self.other)
        self.client.force_login(self.maker)
        self.assertEqual(self.client.get(reverse('order_detail', args=[order.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('order_action', args=[order.pk, 'submit'])).status_code, 404)
        response = self.client.get(reverse('sales'))
        self.assertNotContains(response, order.number)

    def test_missing_permission_returns_403(self):
        viewer = User.objects.create_user('no-permissions')
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('sales')).status_code, 403)

    def test_order_form_persists_lines_and_checks_partner_kind(self):
        self.client.force_login(self.maker)
        Product.objects.filter(pk=self.product.pk).update(gst_rate=18)
        data = {'site': self.site.pk, 'partner': self.dealer.pk, 'due_date': (timezone.localdate() + timedelta(days=3)).isoformat(), 'freight_terms': 'ex_works', 'lines-TOTAL_FORMS': 1, 'lines-INITIAL_FORMS': 0, 'lines-MIN_NUM_FORMS': 1, 'lines-MAX_NUM_FORMS': 50, 'lines-0-product': self.product.pk, 'lines-0-quantity': '12.500', 'lines-0-rate': '125.75'}
        response = self.client.post(reverse('order_new', args=['Sale']), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Order.objects.get().total, Decimal('1571.87500'))
        data['partner'] = self.supplier.pk
        self.assertEqual(self.client.post(reverse('order_new', args=['Sale']), data).status_code, 200)
        self.assertEqual(Order.objects.count(), 1)

    def test_cross_site_form_tampering_rejected(self):
        self.client.force_login(self.maker)
        response = self.client.post(reverse('opening'), {'site': self.other.pk, 'product': self.product.pk, 'quantity': '10', 'reference': 'bad', 'notes': 'test'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(StockMovement.objects.exists())

    def test_opening_form_duplicate_reference(self):
        self.client.force_login(self.admin)
        data = {'site': self.site.pk, 'product': self.product.pk, 'quantity': '10', 'reference': 'opening-1', 'notes': 'Test opening'}
        self.assertEqual(self.client.post(reverse('opening'), data).status_code, 302)
        response = self.client.post(reverse('opening'), data)
        self.assertContains(response, 'already been posted')
        self.assertEqual(StockBalance.objects.get().quantity, 10)

    def test_export_scoped_and_audited(self):
        self.opening()
        move_stock(self.admin, self.other, self.product2, Decimal(100), 'other', 'Opening')
        self.client.force_login(self.maker)
        response = self.client.get('/reports/?export=csv')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'S1', response.content)
        self.assertNotIn(b'S2', response.content)
        self.assertTrue(AuditEvent.objects.filter(action='Stock ledger exported').exists())

    def test_state_changes_require_post_and_csrf(self):
        order = self.make_order()
        self.client.force_login(self.maker)
        url = reverse('order_action', args=[order.pk, 'submit'])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.maker)
        self.assertEqual(client.post(url).status_code, 403)

    def test_base_unit_locked_after_use(self):
        self.opening()
        self.client.force_login(self.admin)
        response = self.client.post(reverse('products_edit', args=[self.product.pk]), {'code': 'C1', 'name': 'Cement', 'category': 'Cement', 'grade': 'OPC 53', 'unit': 'BAG', 'bag_weight_kg': 25, 'reorder_level': 0, 'active': 'on'})
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.unit, 'MT')
        self.assertIsNone(self.product.bag_weight_kg)
