from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User, Group, Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import logistics as L
from .models import (Site, SiteAccess, Product, Partner, Order, OrderLine, StockBalance, StockMovement, Vehicle, Driver, Dispatch, Weighment, Invoice,
                     Organization, AuditEvent)
from .services import transition_order

ALL_CHECKS = {k: True for k, _ in L.GATE_CHECKS}


def user_with(name, *codes):
    u = User.objects.create_user(name, f'{name}@example.com', 'Original-Password-816!')
    g = Group.objects.create(name=f'g-{name}')
    g.permissions.set(Permission.objects.filter(content_type__app_label='erp', codename__in=['view_dispatch', *codes]))
    u.groups.add(g)
    return User.objects.get(pk=u.pk)


@override_settings(EMAIL_OTP_ENABLED=False)
class LogisticsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'gstin': '23ABCDE1234F1Z5', 'default_bag_weight': 50})
        cls.site = Site.objects.create(code='PLT-01', name='Plant', kind='Plant', state='23', gstin='23ABCDE1234F1Z5', uses_weighbridge=True,
                                       stock_post_event='gate_out', weight_tolerance_pct=Decimal('0.5'), scale_id='WB-1')
        cls.cement = Product.objects.create(code='OPC', name='OPC 53', category='Cement', unit='BAG', bag_weight_kg=50, gst_rate=18, hsn='2523')
        cls.dealer = Partner.objects.create(code='D1', name='Dealer', kind='Dealer', state='23', credit_limit=10 ** 8)
        cls.trans = Partner.objects.create(code='T1', name='Narmada Logistics', kind='Transporter')
        far = timezone.localdate() + timedelta(days=200)
        cls.truck = Vehicle.objects.create(registration='MP04GA1234', vehicle_type='truck10', capacity_mt=25, transporter=cls.trans, rc_valid_until=far,
                                           insurance_valid_until=far, permit_valid_until=far, fitness_valid_until=far, puc_valid_until=far)
        cls.driver = Driver.objects.create(name='Ramesh', mobile='9876543210', licence_number='MP0420190012345', licence_valid_until=far)
        cls.admin = User.objects.create_superuser('lg-admin', 'lg@example.com', 'Original-Password-816!')
        cls.planner = user_with('planner', 'add_dispatch')
        cls.gate = user_with('gate', 'gate_dispatch')
        cls.wb = user_with('wb', 'weigh_dispatch')
        cls.loader = user_with('loader', 'load_dispatch')
        cls.sup = user_with('sup', 'approve_weight_exception', 'weigh_dispatch')
        cls.acct = user_with('acct', 'post_invoice')
        cls.pod = user_with('podu', 'pod_dispatch')
        for actor in [cls.planner, cls.gate, cls.wb, cls.loader, cls.sup, cls.acct, cls.pod]:
            SiteAccess.objects.create(user=actor, site=cls.site)

    def setUp(self):
        StockBalance.objects.create(site=self.site, product=self.cement, quantity=1000)
        self.order = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=self.admin, status='Approved')
        self.line = OrderLine.objects.create(order=self.order, product=self.cement, quantity=600, rate=380, gst_rate=18, hsn='2523')

    def run_to_weighed(self, qty=400, loaded=None, tare=9000, gross=None):
        d = L.plan(self.planner, self.order, self.truck, self.driver, {self.line: qty})
        L.gate_in(self.gate, d.pk, ALL_CHECKS)
        L.weigh(self.wb, d.pk, 'tare', tare, 'scale')
        L.start_loading(self.loader, d.pk, 'Bay 1')
        L.complete_loading(self.loader, d.pk, {d.lines.get().pk: loaded if loaded is not None else qty}, seal='S1')
        L.weigh(self.wb, d.pk, 'gross', gross or tare + (loaded if loaded is not None else qty) * 50, 'scale')
        return Dispatch.objects.get(pk=d.pk)

    def test_full_trip_partial_supply_and_stock_once(self):
        d = self.run_to_weighed()
        self.assertEqual((d.status, d.net_kg, d.exception), ('weighed', 20000, ''))
        self.assertEqual(StockBalance.objects.get().quantity, 1000)  # plant posts at gate-out
        with self.assertRaisesMessage(ValidationError, 'Post the tax invoice'):
            L.gate_out(self.gate, d.pk)
        inv = L.invoice(self.acct, d.pk, '1234 5678 9012')
        self.assertEqual((inv.dispatch, inv.vehicle_number, inv.transporter, inv.eway_bill), (d, 'MP04GA1234', 'Narmada Logistics', '123456789012'))
        self.assertEqual(inv.taxable, Decimal('152000.00'))  # 400 bags × 380
        L.gate_out(self.gate, d.pk)
        self.assertEqual(StockBalance.objects.get().quantity, 600)
        self.assertEqual(StockMovement.objects.filter(source__startswith=f'dispatch:{d.pk}:').count(), 1)
        self.order.refresh_from_db(); self.truck.refresh_from_db()
        self.assertEqual((self.order.status, self.truck.tare_kg), ('Part dispatched', 9000))
        self.assertEqual(self.line.pending_quantity, 200)
        with self.assertRaisesMessage(ValidationError, 'only 200'):
            L.plan(self.planner, self.order, self.truck, self.driver, {self.line: 300})
        # POD rules
        with self.assertRaisesMessage(ValidationError, 'Describe the shortage'):
            L.deliver(self.pod, d.pk, 'Mahesh', shortage=2)
        L.deliver(self.pod, d.pk, 'Mahesh', '9123456780', shortage=2, notes='2 bags short')
        self.assertEqual(Dispatch.objects.get(pk=d.pk).status, 'delivered')
        # Second truck completes the order
        d2 = self.run_to_weighed(qty=200, tare=9100)
        L.invoice(self.acct, d2.pk)
        L.gate_out(self.gate, d2.pk)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'Completed')
        self.assertEqual(Invoice.objects.filter(order=self.order).count(), 2)

    def test_trip_exceptions_diversion_and_refusal(self):
        from erp import incidents as X
        from erp.models import DispatchIncident, ReturnRequest
        d = self.run_to_weighed(qty=100)
        with self.assertRaisesMessage(ValidationError, 'in transit'):
            X.report(self.planner, d.pk, 'delay', 'Late start')
        L.invoice(self.acct, d.pk); L.gate_out(self.gate, d.pk)
        b = X.report(self.pod, d.pk, 'breakdown', 'Clutch failure', 'Rewa bypass')
        with self.assertRaisesMessage(ValidationError, 'how it was resolved'):
            X.resolve(self.pod, b.pk, '')
        X.resolve(self.pod, b.pk, 'Repaired, moving')
        with self.assertRaisesMessage(ValidationError, 'new delivery address'):
            X.report(self.pod, d.pk, 'diversion', 'Dealer asks other godown')
        div = X.report(self.pod, d.pk, 'diversion', 'Dealer asks other godown', new_ship_to='Godown 2, Satna')
        with self.assertRaisesMessage(ValidationError, 'already waiting'):
            X.report(self.pod, d.pk, 'refused', 'x')
        X.decide(self.admin, div.pk, True, 'EWB Part-B updated')
        ref = X.report(self.pod, d.pk, 'refused', 'Dealer refused: wrong grade ordered')
        with self.assertRaises(PermissionDenied):
            X.decide(self.pod, ref.pk, True)
        X.decide(self.admin, ref.pk, True)
        d.refresh_from_db(); ref.refresh_from_db()
        self.assertEqual(d.status, 'returned')
        rma = ReturnRequest.objects.get(pk=ref.return_request_id)
        self.assertEqual((rma.status, rma.lines.get().quantity), ('requested', 100))
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('dispatch_detail', args=[d.pk])), rma.number)

    def test_documents_checklist_and_segregation(self):
        Vehicle.objects.filter(pk=self.truck.pk).update(puc_valid_until=timezone.localdate() - timedelta(days=1))
        with self.assertRaisesMessage(ValidationError, 'PUC expired'):
            L.plan(self.planner, self.order, Vehicle.objects.get(pk=self.truck.pk), self.driver, {self.line: 100})
        Vehicle.objects.filter(pk=self.truck.pk).update(puc_valid_until=timezone.localdate() + timedelta(days=9))
        d = L.plan(self.planner, self.order, Vehicle.objects.get(pk=self.truck.pk), self.driver, {self.line: 100})
        self.assertEqual(d.token, 1)
        with self.assertRaises(PermissionDenied):
            L.gate_in(self.wb, d.pk, ALL_CHECKS)  # weighbridge cannot run the gate
        with self.assertRaisesMessage(ValidationError, 'Gate checklist incomplete'):
            L.gate_in(self.gate, d.pk, {'licence': True})
        with self.assertRaisesMessage(ValidationError, 'is already on'):
            L.plan(self.planner, self.order, self.truck, self.driver, {self.line: 10})  # same truck twice
        L.gate_in(self.gate, d.pk, ALL_CHECKS)
        with self.assertRaises(PermissionDenied):
            L.weigh(self.gate, d.pk, 'tare', 9000, 'scale')  # gate cannot weigh
        with self.assertRaisesMessage(ValidationError, 'Manual entry needs a reason'):
            L.weigh(self.wb, d.pk, 'tare', 9000, 'manual')
        with self.assertRaisesMessage(ValidationError, 'more than zero'):
            L.weigh(self.wb, d.pk, 'tare', 0, 'scale')

    def test_reweigh_keeps_original_and_overload_capacity(self):
        with self.assertRaisesMessage(ValidationError, 'more than'):
            L.plan(self.planner, self.order, self.truck, self.driver, {self.line: 600})  # 30 MT > 25 MT capacity
        d = self.run_to_weighed(qty=400)
        first = d.gross
        with self.assertRaisesMessage(ValidationError, 'reweigh needs a reason'):
            L.weigh(self.wb, d.pk, 'gross', 29050, 'scale')
        L.weigh(self.wb, d.pk, 'gross', 29050, 'scale', 'Driver stepped on the platform')
        d.refresh_from_db(); first.refresh_from_db()
        self.assertEqual((first.weight_kg, first.superseded_by.weight_kg, d.gross.weight_kg), (29000, 29050, 29050))
        self.assertEqual(Weighment.objects.filter(dispatch=d).count(), 3)
        self.assertEqual(d.exception, '')  # +50 kg within ±100 kg tolerance
        with self.assertRaisesMessage(ValidationError, 'must be more than tare'):
            L.weigh(self.wb, d.pk, 'gross', 8000, 'scale', 'test')

    def test_tolerance_exception_needs_other_person(self):
        d = self.run_to_weighed(qty=400, gross=9000 + 20000 + 600)  # +600 kg vs ±100 allowed
        self.assertTrue(d.exception_open)
        self.assertIn('Net 20,600 kg vs loaded 20,000 kg', d.exception)
        with self.assertRaisesMessage(ValidationError, 'supervisor approval'):
            L.invoice(self.acct, d.pk)
        sup_who_weighed = self.sup
        L.weigh(sup_who_weighed, d.pk, 'gross', 29600, 'scale', 'Recheck')  # supervisor reweighed → cannot approve own reading
        with self.assertRaisesMessage(ValidationError, 'cannot approve'):
            L.approve_exception(self.sup, d.pk, 'ok')
        other = user_with('sup2', 'approve_weight_exception')
        SiteAccess.objects.create(user=other, site=self.site)
        with self.assertRaisesMessage(ValidationError, 'Write why'):
            L.approve_exception(other, d.pk, '  ')
        L.approve_exception(other, d.pk, '12 bags checked at 50.5 kg')
        L.invoice(self.acct, d.pk)
        L.gate_out(self.gate, d.pk)
        self.assertTrue(AuditEvent.objects.filter(action='Weight exception approved', reference=d.number).exists())

    def test_load_complete_posting_and_cancel_reversal(self):
        Site.objects.filter(pk=self.site.pk).update(stock_post_event='load_complete')
        self.order.refresh_from_db()
        d = L.plan(self.planner, self.order, self.truck, self.driver, {self.line: 100})
        L.gate_in(self.gate, d.pk, ALL_CHECKS)
        L.weigh(self.wb, d.pk, 'tare', 9000, 'scale')
        L.start_loading(self.loader, d.pk)
        with self.assertRaisesMessage(ValidationError, 'between 0 and planned'):
            L.complete_loading(self.loader, d.pk, {d.lines.get().pk: 150})
        L.complete_loading(self.loader, d.pk, {d.lines.get().pk: 90})
        self.assertEqual(StockBalance.objects.get().quantity, 910)  # issued at load complete
        with self.assertRaisesMessage(ValidationError, 'Give a reason'):
            L.cancel(self.planner, d.pk, '')
        L.cancel(self.planner, d.pk, 'Customer postponed')
        self.assertEqual(StockBalance.objects.get().quantity, 1000)  # put back
        self.assertEqual(self.line.pending_quantity, 600)

    def test_order_rules_and_screens(self):
        with self.assertRaisesMessage(ValidationError, 'gate & weighbridge'):
            transition_order(self.admin, self.order.pk, 'post')  # no shortcut around the gate
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('order_detail', args=[self.order.pk])), reverse('dispatch_new', args=[self.order.pk]))
        r = self.client.post(reverse('dispatch_new', args=[self.order.pk]), {'vehicle': self.truck.pk, 'driver': self.driver.pk, f'qty_{self.line.pk}': '100', 'bay': 'Bay 2'})
        d = Dispatch.objects.get()
        self.assertRedirects(r, reverse('dispatch_detail', args=[d.pk]))
        self.assertContains(self.client.get(reverse('dispatch_board')), 'MP04GA1234')
        self.client.post(reverse('dispatch_action', args=[d.pk, 'gate_in']), {k: 'on' for k in ALL_CHECKS})
        self.client.post(reverse('dispatch_action', args=[d.pk, 'tare']), {'weight_kg': '9000', 'source': 'manual', 'reason': 'Scale offline'})
        self.client.post(reverse('dispatch_action', args=[d.pk, 'start_loading']), {'bay': 'Bay 2'})
        self.client.post(reverse('dispatch_action', args=[d.pk, 'complete_loading']), {f'qty_{d.lines.get().pk}': '100', 'seal': 'S9'})
        self.client.post(reverse('dispatch_action', args=[d.pk, 'gross']), {'weight_kg': '14000', 'source': 'scale'})
        self.assertEqual(Dispatch.objects.get().status, 'weighed')
        self.assertContains(self.client.get(reverse('dispatch_print', args=[d.pk, 'slip'])), 'Weighment slip')
        self.assertRedirects(self.client.get(reverse('dispatch_print', args=[d.pk, 'pass'])), reverse('dispatch_detail', args=[d.pk]))
        self.client.post(reverse('dispatch_action', args=[d.pk, 'invoice']), {'eway_bill': ''})
        self.client.post(reverse('dispatch_action', args=[d.pk, 'gate_out']))
        self.assertContains(self.client.get(reverse('dispatch_print', args=[d.pk, 'pass'])), 'Gate pass')
        self.assertEqual(Dispatch.objects.get().status, 'gate_out')
        transition_order(self.admin, self.order.pk, 'close', reason='Customer does not need the balance')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, 'Completed')
        # Gate user sees the board only for plants they can access
        SiteAccess.objects.filter(user=self.gate).delete()  # simulate access revocation
        self.client.force_login(self.gate)
        self.assertEqual(self.client.get(reverse('dispatch_detail', args=[d.pk])).status_code, 404)
        SiteAccess.objects.create(user=self.gate, site=self.site)
        self.assertEqual(self.client.get(reverse('dispatch_detail', args=[d.pk])).status_code, 200)

    def test_vehicle_and_driver_forms(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('vehicle_new'), {'registration': 'mp 09 hh 4321', 'vehicle_type': 'truck6', 'capacity_mt': '16', 'owner': 'transporter', 'transporter': ''})
        self.assertContains(r, 'Choose the transporter')
        r = self.client.post(reverse('vehicle_new'), {'registration': 'mp 09 hh 4321', 'vehicle_type': 'truck6', 'capacity_mt': '16', 'owner': 'customer', 'active': 'on'})
        v = Vehicle.objects.get(registration='MP09HH4321')
        self.assertEqual(len(v.document_issues()), 5)
        self.assertFalse(v.can_enter)
        r = self.client.post(reverse('driver_new'), {'name': 'Suresh', 'mobile': '+91 98765 43210', 'licence_number': 'MP0420190099999', 'licence_valid_until': '2030-01-01', 'active': 'on'})
        self.assertContains(r, 'Another driver has this mobile')
        page = self.client.get(reverse('vehicles'))
        self.assertContains(page, 'MP09HH4321')
        self.assertContains(page, 'RC missing')
