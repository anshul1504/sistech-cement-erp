from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import freight as F
from . import logistics as L
from .models import (Site, Product, Partner, Order, OrderLine, StockBalance, Vehicle, Driver, Dispatch, FreightRate, FreightBill, Organization)

CHECKS = {k: True for k, _ in L.GATE_CHECKS}


@override_settings(EMAIL_OTP_ENABLED=False)
class FreightTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'gstin': '23ABCDE1234F1Z5'})
        cls.site = Site.objects.create(code='PLT', name='Plant', kind='Plant', state='23', gstin='23ABCDE1234F1Z5', uses_weighbridge=True)
        cls.cement = Product.objects.create(code='OPC', name='OPC', category='Cement', unit='BAG', bag_weight_kg=50, gst_rate=18, hsn='2523')
        cls.dealer = Partner.objects.create(code='D1', name='Dealer', kind='Dealer', state='23', district='Bhopal', credit_limit=10 ** 8)
        cls.trans = Partner.objects.create(code='T1', name='Narmada', kind='Transporter')
        far = timezone.localdate() + timedelta(days=100)
        docs = dict(rc_valid_until=far, insurance_valid_until=far, permit_valid_until=far, fitness_valid_until=far, puc_valid_until=far)
        cls.truck = Vehicle.objects.create(registration='MP04GA1', vehicle_type='truck10', capacity_mt=30, transporter=cls.trans, **docs)
        cls.truck2 = Vehicle.objects.create(registration='MP04GA2', vehicle_type='truck12', capacity_mt=30, transporter=cls.trans, **docs)
        cls.driver = Driver.objects.create(name='R', mobile='9876543210', licence_number='MP0420190012345', licence_valid_until=far)
        cls.driver2 = Driver.objects.create(name='S', mobile='9876543211', licence_number='MP0420190012346', licence_valid_until=far)
        cls.admin = User.objects.create_superuser('fr-admin', 'fr@example.com', 'Original-Password-816!')
        cls.checker = User.objects.create_superuser('fr-check', 'fc@example.com', 'Original-Password-816!')
        start = timezone.localdate() - timedelta(days=5)
        cls.lane = FreightRate.objects.create(origin=cls.site, destination='Bhopal', basis='per_mt', rate=600, min_mt=15, detention_per_day=1000, unloading_charge=500, valid_from=start)
        cls.special = FreightRate.objects.create(origin=cls.site, destination='Bhopal', transporter=cls.trans, vehicle_type='truck12', basis='per_trip', rate=11000, valid_from=start)
        StockBalance.objects.create(site=cls.site, product=cls.cement, quantity=5000)

    def trip(self, truck, driver, qty, terms='for_destination', short=0):
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=self.admin, status='Approved', freight_terms=terms)
        line = OrderLine.objects.create(order=o, product=self.cement, quantity=qty, rate=400, gst_rate=18)
        d = L.plan(self.admin, o, truck, driver, {line: qty})
        L.gate_in(self.admin, d.pk, CHECKS); L.weigh(self.admin, d.pk, 'tare', 9000, 'scale'); L.start_loading(self.admin, d.pk)
        L.complete_loading(self.admin, d.pk, {d.lines.get().pk: qty}); L.weigh(self.admin, d.pk, 'gross', 9000 + qty * 50, 'scale')
        L.invoice(self.admin, d.pk); L.gate_out(self.admin, d.pk)
        if short:
            L.deliver(self.admin, d.pk, 'Rcv', shortage=short, notes='short')
        return Dispatch.objects.get(pk=d.pk)

    def test_rate_snapshot_and_bill_check(self):
        a = self.trip(self.truck, self.driver, 400, short=4)  # 20 MT × 600 = 12,000
        b = self.trip(self.truck2, self.driver2, 200)  # truck12 + transporter → per-trip 11,000
        c = self.trip(self.truck, self.driver, 100)  # 5 MT → minimum 15 MT = 9,000
        ex = self.trip(self.truck2, self.driver2, 100, terms='ex_works')
        self.assertEqual((a.freight_amount, a.freight_rate, a.freight_payable_by), (Decimal('12000.00'), self.lane, 'company'))
        self.assertEqual((b.freight_amount, b.freight_rate), (Decimal('11000.00'), self.special))
        self.assertEqual(c.freight_amount, Decimal('9000.00'))
        self.assertEqual((ex.freight_payable_by, ex.freight_amount), ('customer', None))
        self.assertNotIn(ex, list(F.billable_trips(self.trans)))
        # Freight frozen: a new lane rate does not change past trips
        FreightRate.objects.filter(pk=self.lane.pk).update(rate=900)
        a.refresh_from_db()
        self.assertEqual(a.freight_amount, Decimal('12000.00'))
        rows = {a: {'claimed': Decimal('12500'), 'days': 2, 'extra': Decimal('3000'), 'reason': 'Detention at site'},
                b: {'claimed': Decimal('11000'), 'days': 0, 'extra': Decimal('0'), 'reason': ''}}
        with self.assertRaisesMessage(ValidationError, 'not billable'):
            F.create_bill(self.admin, self.trans, 'NL-1', timezone.localdate(), {**rows, ex: rows[b]})
        bill = F.create_bill(self.admin, self.trans, 'NL-1', timezone.localdate(), rows, advance=5000)
        line_a = bill.lines.get(dispatch=a)
        self.assertEqual((line_a.allowed_extra, line_a.shortage_recovery), (Decimal('2500'), Decimal('1600.00')))  # 2×1000+500 cap; 4 bags × ₹400
        # (12,000 capped + 2,500 − 1,600) + 11,000 − 5,000 advance
        self.assertEqual(bill.payable, Decimal('18900.00'))
        self.assertEqual(bill.variance, Decimal('3500.00'))
        with self.assertRaisesMessage(ValidationError, 'already entered'):
            F.create_bill(self.admin, self.trans, 'nl-1', timezone.localdate(), {c: rows[b]})
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            F.decide(self.admin, bill.pk, True, 'ok')
        with self.assertRaisesMessage(ValidationError, 'above contract'):
            F.decide(self.checker, bill.pk, True, '')
        F.decide(self.checker, bill.pk, True, 'Detention verified with dealer')
        self.assertEqual(FreightBill.objects.get(pk=bill.pk).status, 'approved')
        self.assertEqual(list(F.billable_trips(self.trans)), [c])

    def test_reject_releases_trips_and_screens(self):
        a = self.trip(self.truck, self.driver, 300)
        self.client.force_login(self.admin)
        r = self.client.post(reverse('freight_bill_new', args=[self.trans.pk]), {'bill_number': 'NL-9', 'bill_date': timezone.localdate().isoformat(), 'advance': '0',
                                                                             f'pick_{a.pk}': 'on', f'claimed_{a.pk}': '9000', f'days_{a.pk}': '0', f'extra_{a.pk}': '200', f'reason_{a.pk}': ''})
        self.assertContains(r, 'give the reason for the extra')
        r = self.client.post(reverse('freight_bill_new', args=[self.trans.pk]), {'bill_number': 'NL-9', 'bill_date': timezone.localdate().isoformat(), 'advance': '0',
                                                                             f'pick_{a.pk}': 'on', f'claimed_{a.pk}': '9000', f'days_{a.pk}': '0', f'extra_{a.pk}': '0'})
        bill = FreightBill.objects.get()
        self.assertRedirects(r, reverse('freight_bill_detail', args=[bill.pk]))
        self.assertNotContains(self.client.get(reverse('freight_bill_detail', args=[bill.pk])), 'Approve payable')  # maker cannot see decide
        self.client.force_login(self.checker)
        self.client.post(reverse('freight_bill_decide', args=[bill.pk, 'reject']), {'note': ''})
        self.assertEqual(FreightBill.objects.get().status, 'submitted')
        self.client.post(reverse('freight_bill_decide', args=[bill.pk, 'reject']), {'note': 'Wrong LR'})
        self.assertEqual(FreightBill.objects.get().status, 'rejected')
        self.assertEqual(list(F.billable_trips(self.trans)), [a])
        for url in ['freight_rates', 'freight_bills']:
            self.assertEqual(self.client.get(reverse(url)).status_code, 200)
        self.assertContains(self.client.get(reverse('dispatch_detail', args=[a.pk])), 'contract')
        # Rates used on trips are locked except end date
        r = self.client.post(reverse('freight_rate_edit', args=[self.lane.pk]), {'valid_to': timezone.localdate().isoformat(), 'active': 'on'})
        self.lane.refresh_from_db()
        self.assertEqual((self.lane.rate, self.lane.valid_to), (Decimal('600.00'), timezone.localdate()))
