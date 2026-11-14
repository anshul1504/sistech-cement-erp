from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .invoice_views import post_invoice
from .ledger import balance, open_invoices, ageing, ledger_rows, exposure
from .models import (Site, Product, Partner, Order, OrderLine, Invoice, Receipt, CreditNote, StockBalance, Organization, AuditEvent)
from .services import transition_order


@override_settings(EMAIL_OTP_ENABLED=False)
class ReceivableTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('rc-admin', 'rc@example.com', 'Original-Password-816!')
        cls.maker = User.objects.create_superuser('rc-maker', 'rm@example.com', 'Original-Password-816!')
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'gstin': '23ABCDE1234F1Z5'})
        cls.site = Site.objects.create(code='PLT-01', name='Plant', kind='Plant', state='23', gstin='23ABCDE1234F1Z5')
        cls.cement = Product.objects.create(code='OPC', name='OPC 53', category='Cement', unit='BAG', gst_rate=18, hsn='2523')
        cls.dealer = Partner.objects.create(code='D1', name='Dealer', kind='Dealer', state='23', payment_terms_days=30, credit_limit=200000)

    def setUp(self):
        self.client.force_login(self.admin)

    def invoice(self, qty=100, rate=Decimal('100'), days_ago=0):
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=self.admin, status='Completed')
        OrderLine.objects.create(order=o, product=self.cement, quantity=qty, rate=rate, gst_rate=18, hsn='2523')
        inv = post_invoice(self.admin, o.pk, {})
        if days_ago:
            Invoice.objects.filter(pk=inv.pk).update(date=timezone.localdate() - timedelta(days=days_ago), due_date=timezone.localdate() - timedelta(days=days_ago - 30))
        return Invoice.objects.get(pk=inv.pk)  # 100 × 100 × 1.18 = 11,800

    def receive(self, amount, mode='neft', ref=None, **extra):
        data = {'partner': self.dealer.pk, 'date': timezone.localdate().isoformat(), 'amount': str(amount), 'mode': mode,
                'reference': ref if ref is not None else f'UTR{amount}{mode}', 'instrument_date': '', 'drawn_on': '', 'deposited_to': '', 'notes': '', **extra}
        return self.client.post(reverse('receipt_new'), data)

    def test_fifo_settlement_and_advance(self):
        old, new = self.invoice(days_ago=50), self.invoice()
        self.assertEqual(old.grand_total, Decimal('11800'))
        r = self.receive(15000)
        rec = Receipt.objects.get()
        self.assertRedirects(r, reverse('receipt_detail', args=[rec.pk]))
        self.assertTrue(rec.number.startswith('RCTHO/'))
        old.refresh_from_db(); new.refresh_from_db()
        self.assertEqual((old.outstanding, new.outstanding), (Decimal('0'), Decimal('8600')))
        self.receive(10000, ref='UTR2')  # 8,600 settles, 1,400 stays as advance
        self.assertEqual(balance(self.dealer), Decimal('-1400'))
        third = self.invoice()  # advance applies automatically
        self.assertEqual(third.outstanding, Decimal('10400'))
        self.assertContains(self.client.get(reverse('partner_ledger', args=[self.dealer.pk])), 'Customer ledger')

    def test_receipt_rules(self):
        self.invoice()
        r = self.receive(199999, mode='cash', ref='')
        self.assertEqual(r.status_code, 302)
        r = self.receive(10, mode='cash', ref='')
        self.assertContains(r, 's.269ST')  # same-day cash total reaches ₹2 lakh
        r = self.receive(500, mode='neft', ref='')
        self.assertContains(r, 'Enter the UTR')
        r = self.receive(500, mode='cheque', ref='000123')
        self.assertContains(r, 'Enter the cheque / DD date')
        self.receive(500, mode='upi', ref='UPI1')
        self.assertContains(self.receive(700, mode='upi', ref='upi1'), 'possible duplicate')
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertContains(self.receive(1, ref='X', date=future), 'cannot be in the future')

    def test_cheque_bounce_reverses(self):
        inv = self.invoice()
        self.receive(11800, mode='cheque', ref='445566', instrument_date=timezone.localdate().isoformat())
        rec = Receipt.objects.get()
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, 0)
        self.client.post(reverse('receipt_bounce', args=[rec.pk]), {'reason': 'Insufficient funds'})
        rec.refresh_from_db(); inv.refresh_from_db()
        self.assertEqual((rec.status, inv.outstanding, balance(self.dealer)), ('bounced', Decimal('11800'), Decimal('11800')))
        opening, rows = ledger_rows(self.dealer)
        self.assertEqual([r['kind'] for r in rows], ['Invoice', 'Receipt', 'Cheque bounced'])
        self.assertEqual(rows[-1]['balance'], Decimal('11800'))
        self.assertTrue(AuditEvent.objects.filter(action='Cheque bounced').exists())
        # A NEFT receipt cannot be "bounced"
        self.receive(100, ref='N1')
        self.client.post(reverse('receipt_bounce', args=[Receipt.objects.get(reference='N1').pk]))
        self.assertEqual(Receipt.objects.get(reference='N1').status, 'posted')

    def test_credit_notes(self):
        inv = self.invoice()
        line = inv.lines.get()
        url = reverse('credit_note_new', args=[inv.pk])
        r = self.client.post(url, {'reason': 'rate', f'value_{line.pk}': '20000'})
        self.assertContains(r, 'credit must be between')
        r = self.client.post(url, {'reason': 'rate', f'value_{line.pk}': '1000'})
        cn = CreditNote.objects.get()
        self.assertRedirects(r, reverse('credit_note_detail', args=[cn.pk]))
        self.assertLessEqual(len(cn.number), 16)
        self.assertEqual((cn.taxable, cn.cgst, cn.sgst, cn.total), (Decimal('1000.00'), Decimal('90.00'), Decimal('90.00'), Decimal('1180')))
        inv.refresh_from_db()
        self.assertEqual(inv.outstanding, Decimal('10620'))
        # Goods return: quantity capped, stock comes back, value defaults to qty × rate
        StockBalance.objects.create(site=self.site, product=self.cement, quantity=0)
        self.assertContains(self.client.post(url, {'reason': 'return', f'qty_{line.pk}': '150'}), 'returned quantity must be between')
        self.client.post(url, {'reason': 'return', f'qty_{line.pk}': '10'})
        ret = CreditNote.objects.get(reason='return')
        self.assertTrue(ret.returns_stock)
        self.assertEqual(ret.taxable, Decimal('1000.00'))
        self.assertEqual(StockBalance.objects.get(site=self.site, product=self.cement).quantity, 10)
        # Remaining room is 8,000 taxable; more is refused
        self.assertContains(self.client.post(url, {'reason': 'scheme', f'value_{line.pk}': '8000.01'}), 'credit must be between')
        self.assertEqual(self.client.get(reverse('credit_note_detail', args=[cn.pk])).status_code, 200)
        self.assertContains(self.client.get(reverse('credit_notes')), cn.number)

    def test_overdue_and_limit_block_approval(self):
        self.invoice(days_ago=45)  # due 15 days ago
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=self.maker, status='Submitted')
        OrderLine.objects.create(order=o, product=self.cement, quantity=10, rate=100, gst_rate=18)
        with self.assertRaisesMessage(ValidationError, 'past due'):
            transition_order(self.admin, o.pk, 'approve')
        self.assertContains(self.client.get(reverse('order_detail', args=[o.pk])), 'Approval will be blocked')
        self.receive(11800, ref='PAY1')
        transition_order(self.admin, o.pk, 'approve', backorder_reason='Stock due from partner plant')
        self.assertEqual(Order.objects.get(pk=o.pk).status, 'Approved')
        self.assertEqual(exposure(self.dealer)['pipeline'], Decimal('1180'))
        big = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=self.maker, status='Submitted')
        OrderLine.objects.create(order=big, product=self.cement, quantity=2000, rate=100, gst_rate=18)  # 2,36,000 > 2,00,000 limit
        with self.assertRaisesMessage(ValidationError, 'Credit limit exceeded'):
            transition_order(self.admin, big.pk, 'approve')

    def test_outstanding_page_and_ageing(self):
        self.invoice(days_ago=100)  # 70 days late
        self.invoice()
        age = ageing(open_invoices(self.dealer))
        self.assertEqual((age['d90'], age['current'], age['overdue']), (Decimal('11800'), Decimal('11800'), Decimal('11800')))
        page = self.client.get(reverse('outstanding'))
        self.assertEqual(page.context['rows'][0]['age']['total'], Decimal('23600'))
        csv = self.client.get(reverse('outstanding') + '?export=csv').content.decode('utf-8-sig')
        self.assertIn('Dealer,D1', csv)
        self.assertContains(self.client.get(reverse('partner_detail', args=[self.dealer.pk])), 'Ledger balance')
