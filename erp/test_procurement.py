from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import procurement as P
from .models import (Site, SiteAccess, Product, Partner, Order, OrderLine, StockBalance, PurchaseRequest, RFQ, SupplierQuote, Organization)
from .services import transition_order

TODAY = timezone.localdate


@override_settings(EMAIL_OTP_ENABLED=False)
class ProcurementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'po_approval_limit': 500000})
        cls.site = Site.objects.create(code='PLT', name='Plant', kind='Plant', state='23')
        cls.coal = Product.objects.create(code='COAL', name='Coal', category='Fuel', unit='MT', gst_rate=5, hsn='2701', reorder_level=100, reorder_quantity=300)
        cls.gyp = Product.objects.create(code='GYP', name='Gypsum', category='Raw material', unit='MT', gst_rate=5, reorder_level=50)
        cls.s1 = Partner.objects.create(code='S1', name='Coal Co', kind='Supplier', state='23', payment_terms_days=30)
        cls.s2 = Partner.objects.create(code='S2', name='Fuel Traders', kind='Supplier', state='22')
        cls.s3 = Partner.objects.create(code='S3', name='Old Supplier', kind='Supplier', state='23')
        cls.maker = User.objects.create_superuser('pr-maker', 'm@example.com', 'Original-Password-816!')
        cls.checker = User.objects.create_superuser('pr-check', 'c@example.com', 'Original-Password-816!')
        cls.officer = User.objects.create_user('pr-officer', password='Original-Password-816!')
        cls.officer.user_permissions.set(Permission.objects.filter(content_type__app_label='erp').exclude(codename='approve_high_value_purchase'))
        SiteAccess.objects.create(user=cls.officer, site=cls.site)
        StockBalance.objects.create(site=cls.site, product=cls.coal, quantity=40)

    def approved_pr(self, qty=200):
        pr = P.save_request(self.maker, self.site, TODAY() + timedelta(days=10), 'manual', 'urgent', 'Kiln fuel', [(self.coal, qty, Decimal('9000'), 'GCV 5500')])
        P.request_action(self.maker, pr.pk, 'submit')
        return P.request_action(self.checker, pr.pk, 'approve')

    def test_request_maker_checker_and_reorder(self):
        rows = P.reorder_suggestions([self.site])
        self.assertEqual([(r['product'], r['suggest']) for r in rows], [(self.coal, Decimal('300.000')), (self.gyp, Decimal('50.000'))])
        pr = P.save_request(self.maker, self.site, TODAY() + timedelta(days=5), 'reorder', 'normal', 'Stock low', [(self.coal, 300, None, '')])
        self.assertEqual(pr.lines.get().stock_at_request, Decimal('40'))
        self.assertNotIn(self.coal, [r['product'] for r in P.reorder_suggestions([self.site])])  # already requested
        with self.assertRaisesMessage(ValidationError, 'listed twice'):
            P.save_request(self.maker, self.site, TODAY(), 'manual', 'normal', 'x', [(self.coal, 1, None, ''), (self.coal, 2, None, '')])
        P.request_action(self.maker, pr.pk, 'submit')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            P.request_action(self.maker, pr.pk, 'approve')
        with self.assertRaisesMessage(ValidationError, 'reason'):
            P.request_action(self.checker, pr.pk, 'reject')
        P.request_action(self.checker, pr.pk, 'reject', 'Use stock from depot')
        self.assertEqual(PurchaseRequest.objects.get(pk=pr.pk).status, 'rejected')
        self.assertIn(self.coal, [r['product'] for r in P.reorder_suggestions([self.site])])  # rejected request no longer counts

    def test_rfq_compare_award_and_po_controls(self):
        pr = self.approved_pr(200)
        line = pr.lines.get()
        with self.assertRaisesMessage(ValidationError, 'only 200'):
            P.create_rfq(self.maker, pr, [self.s1, self.s2], TODAY() + timedelta(days=2), '', {line: 250})
        rfq = P.create_rfq(self.maker, pr, [self.s1, self.s2, self.s3], TODAY() + timedelta(days=2), 'Delivered', {line: 200})
        rl = rfq.lines.get()
        base = dict(reference='Q1', quote_date=TODAY(), valid_until=TODAY() + timedelta(days=10), lead_time_days=5, payment_terms_days=30, notes='')
        with self.assertRaisesMessage(ValidationError, 'enter the quoted rate'):
            P.save_quote(self.maker, rfq, self.s1, {**base, 'freight': Decimal('0')}, {})
        P.save_quote(self.maker, rfq, self.s1, {**base, 'freight': Decimal('20000')}, {rl: (Decimal('8900'), Decimal('5'))})  # landed 1,800,000
        P.save_quote(self.maker, rfq, self.s2, {**base, 'freight': Decimal('0'), 'lead_time_days': 12}, {rl: (Decimal('9000'), Decimal('5'))})  # 1,800,000 — slower
        P.save_quote(self.maker, rfq, self.s3, {**base, 'freight': Decimal('0'), 'valid_until': TODAY() - timedelta(days=1), 'quote_date': TODAY() - timedelta(days=5)},
                     {rl: (Decimal('7000'), Decimal('5'))})
        cmp = P.compare(rfq)
        ranks = {q.supplier: q.rank for q in cmp['quotes']}
        self.assertEqual(ranks, {self.s1: 'L1', self.s2: 'L2', self.s3: 'Expired'})
        q1, q2, q3 = (SupplierQuote.objects.get(rfq=rfq, supplier=s) for s in (self.s1, self.s2, self.s3))
        with self.assertRaisesMessage(ValidationError, 'expired'):
            P.award(self.maker, rfq.pk, q3.pk)
        with self.assertRaisesMessage(ValidationError, 'not the lowest'):
            P.award(self.maker, rfq.pk, q2.pk)
        po = P.award(self.maker, rfq.pk, q1.pk, tolerance=Decimal('2'))
        pl = po.lines.get()
        self.assertEqual((pl.rate, pl.pr_line, po.payment_terms_days, po.receipt_tolerance_pct, po.quote), (Decimal('9000.00'), line, 30, Decimal('2'), q1))
        self.assertEqual(RFQ.objects.get(pk=rfq.pk).status, 'awarded')
        # PO value 18,90,000 > limit: officer (no high-value permission) cannot approve; nor can the maker
        transition_order(self.maker, po.pk, 'submit')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            transition_order(self.maker, po.pk, 'approve')
        with self.assertRaisesMessage(ValidationError, 'approval limit'):
            transition_order(self.officer, po.pk, 'approve')
        transition_order(self.checker, po.pk, 'approve')
        self.assertEqual(PurchaseRequest.objects.get(pk=pr.pk).status, 'closed')
        # Cancelling the PO reopens the request and the RFQ for the next supplier
        transition_order(self.checker, po.pk, 'cancel')
        self.assertEqual((PurchaseRequest.objects.get(pk=pr.pk).status, RFQ.objects.get(pk=rfq.pk).status), ('approved', 'open'))
        po2 = P.award(self.maker, rfq.pk, q2.pk, 'Coal Co cannot supply this month')
        self.assertTrue(po2.is_interstate)

    def test_po_cannot_exceed_request(self):
        pr = self.approved_pr(100)
        line = pr.lines.get()
        with self.assertRaisesMessage(ValidationError, 'why this is bought without'):
            P.direct_po(self.maker, pr, self.s1, '', {line: 100}, {line: Decimal('9000')})
        po = P.direct_po(self.maker, pr, self.s1, 'Rate contract', {line: 50}, {line: Decimal('9000')})
        po2 = P.direct_po(self.maker, pr, self.s1, 'Rate contract', {line: 50}, {line: Decimal('9000')})
        with self.assertRaisesMessage(ValidationError, 'only 0'):
            P.direct_po(self.maker, pr, self.s1, 'Rate contract', {line: 1}, {line: Decimal('9000')})
        OrderLine.objects.filter(order=po2).update(quantity=55)  # edited on the draft
        with self.assertRaisesMessage(ValidationError, 'only 50'):
            transition_order(self.maker, po2.pk, 'submit')
        with self.assertRaisesMessage(ValidationError, 'only 45'):  # drafts count too, so nothing is over-ordered
            transition_order(self.maker, po.pk, 'submit')
        transition_order(self.maker, po2.pk, 'cancel')
        transition_order(self.maker, po.pk, 'submit')
        transition_order(self.officer, po.pk, 'approve')  # 50 × 9,000 × 1.05 = 4,72,500 — within the limit
        self.assertEqual(Order.objects.get(pk=po.pk).status, 'Approved')

    def test_screens(self):
        pr = self.approved_pr(200)
        self.client.force_login(self.maker)
        for url in [reverse('purchase_requests'), reverse('reorder'), reverse('pr_new'), reverse('pr_detail', args=[pr.pk]), reverse('rfqs'),
                    reverse('rfq_new', args=[pr.pk]), reverse('pr_direct', args=[pr.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('pr_new'), {'site': self.site.pk, 'needed_by': (TODAY() + timedelta(days=3)).isoformat(), 'priority': 'normal', 'source': 'manual',
                                                 'purpose': 'Raw mill liners', 'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0', 'lines-MIN_NUM_FORMS': '1',
                                                 'lines-MAX_NUM_FORMS': '40', 'lines-0-product': self.gyp.pk, 'lines-0-quantity': '25', 'lines-0-estimated_rate': '',
                                                 'lines-0-specification': ''})
        new = PurchaseRequest.objects.exclude(pk=pr.pk).get()
        self.assertRedirects(r, reverse('pr_detail', args=[new.pk]))
        self.client.post(reverse('pr_action', args=[new.pk, 'submit']))
        self.assertNotContains(self.client.get(reverse('pr_detail', args=[new.pk])), 'formaction')  # maker sees no approve form
        r = self.client.post(reverse('rfq_new', args=[pr.pk]), {'quotes_due': (TODAY() + timedelta(days=2)).isoformat(), 'suppliers': [self.s1.pk, self.s2.pk],
                                                                f'qty_{pr.lines.get().pk}': '200'})
        rfq = RFQ.objects.get()
        self.assertRedirects(r, reverse('rfq_detail', args=[rfq.pk]))
        rl = rfq.lines.get()
        r = self.client.post(reverse('quote_form', args=[rfq.pk, self.s1.pk]), {'reference': 'A1', 'quote_date': TODAY().isoformat(), 'valid_until': '', 'freight': '0',
                                                                               'lead_time_days': '4', 'payment_terms_days': '15', f'rate_{rl.pk}': '8800', f'gst_{rl.pk}': '5'})
        self.assertRedirects(r, reverse('rfq_detail', args=[rfq.pk]))
        page = self.client.get(reverse('rfq_detail', args=[rfq.pk]))
        self.assertContains(page, 'L1')
        self.assertContains(page, 'Award')
        r = self.client.post(reverse('rfq_award', args=[rfq.pk]), {'quote': SupplierQuote.objects.get().pk, 'tolerance': '1'})
        po = Order.objects.get(kind='Purchase')
        self.assertRedirects(r, reverse('order_detail', args=[po.pk]))
        self.assertContains(self.client.get(reverse('order_detail', args=[po.pk])), pr.number)
        # Site scoping: an officer without access to the site sees nothing
        other = User.objects.create_user('pr-out', password='Original-Password-816!')
        other.user_permissions.set(Permission.objects.filter(codename__in=['view_purchaserequest']))
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('pr_detail', args=[pr.pk])).status_code, 404)
        with self.assertRaises(PermissionDenied):
            P.request_action(other, pr.pk, 'cancel', 'x')
