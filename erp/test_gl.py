from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import gl, inbound as IB, payables as AP, treasury as TR
from .invoice_views import create_invoice
from .models import (Site, Product, Partner, Order, OrderLine, Account, JournalEntry, JournalLine, Receipt, FixedAsset, SupplierPayment,
                     BankStatementLine, Organization, StockBalance)
from .services import move_stock, transition_order

D = Decimal
TODAY = timezone.localdate


def bal(key, partner=None):
    qs = JournalLine.objects.filter(account__system_key=key, entry__status='posted')
    if partner: qs = qs.filter(partner=partner)
    return sum((l.debit - l.credit for l in qs), D('0'))


@override_settings(EMAIL_OTP_ENABLED=False)
class LedgerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23', 'gstin': '23ABCDE1234F1Z5'})
        cls.site = Site.objects.create(code='PLT', name='Plant', kind='Plant', state='23', gstin='23ABCDE1234F1Z5')
        cls.cem = Product.objects.create(code='OPC', name='OPC 53', category='Cement', unit='BAG', bag_weight_kg=50, gst_rate=28, hsn='2523')
        cls.coal = Product.objects.create(code='COAL', name='Coal', category='Fuel', unit='MT', gst_rate=5, hsn='2701')
        cls.dealer = Partner.objects.create(code='D1', name='Dealer', kind='Dealer', state='23', gstin='23AAAAA0000A1Z5', credit_limit=10 ** 8)
        cls.sup = Partner.objects.create(code='S1', name='Coal Co', kind='Supplier', state='23', gstin='23AAACC1234C1Z5', bank_account_number='1234567890', bank_ifsc='SBIN0000001')
        cls.a = User.objects.create_superuser('gl-a', 'a@example.com', 'Original-Password-816!')
        cls.b = User.objects.create_superuser('gl-b', 'b@example.com', 'Original-Password-816!')
        call_command('seed_chart', verbosity=0)
        cls.bank = Account.objects.get(system_key='bank_main')

    def assert_balanced(self):
        tb = gl.trial_balance(None, TODAY())
        self.assertEqual(tb['debit'], tb['credit'])
        self.assertEqual(gl.balance_sheet(TODAY())['difference'], 0)

    def test_sales_cycle_posts_and_balances(self):
        move_stock(self.a, self.site, self.cem, D(1000), 'o1', 'Opening', unit_cost=D(280))
        self.assertEqual((bal('inventory_fg'), bal('opening_equity')), (D('280000.00'), D('-280000.00')))
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.dealer, created_by=self.a)
        line = OrderLine.objects.create(order=o, product=self.cem, quantity=100, rate=350, gst_rate=28, hsn='2523')
        transition_order(self.a, o.pk, 'submit'); transition_order(self.b, o.pk, 'approve'); transition_order(self.b, o.pk, 'post')
        self.assertEqual((bal('cogs'), bal('inventory_fg')), (D('28000.00'), D('252000.00')))
        inv = create_invoice(self.a, o, [line], {})
        je = JournalEntry.objects.get(source=f'inv:{inv.pk}')
        self.assertEqual((bal('ar', self.dealer), bal('sales_cement'), bal('output_cgst') + bal('output_sgst')), (D('44800.00'), D('-35000.00'), D('-9800.00')))
        self.assertEqual(gl.post_invoice(self.a, inv), je)  # idempotent
        r = Receipt.objects.create(number='RCT1', partner=self.dealer, amount=D(20000), mode='cheque', reference='000123', created_by=self.a)
        gl.post_receipt(self.a, r)
        self.assertEqual((bal('ar', self.dealer), bal('bank_main')), (D('24800.00'), D('20000.00')))
        Receipt.objects.filter(pk=r.pk).update(status='bounced')
        gl.reverse_source(self.a, f'rct:{r.pk}', reason='Cheque bounced')
        self.assertEqual(bal('ar', self.dealer), D('44800.00'))
        with self.assertRaisesMessage(ValidationError, 'already been reversed'):
            gl.reverse(self.a, JournalEntry.objects.get(source=f'rct:{r.pk}'))
        pl = gl.profit_and_loss(TODAY().replace(day=1), TODAY())
        self.assertEqual(pl['profit'], D('7000.00'))  # 35,000 sales − 28,000 COGS
        self.assert_balanced()
        self.assertTrue(all(c['diff'] == 0 for c in gl.subledger_check()))

    def test_procure_to_pay_clears_grni_and_payables(self):
        o = Order.objects.create(kind='Purchase', site=self.site, partner=self.sup, created_by=self.a, payment_terms_days=30)
        OrderLine.objects.create(order=o, product=self.coal, quantity=50, rate=9000, gst_rate=5)
        transition_order(self.a, o.pk, 'submit'); transition_order(self.b, o.pk, 'approve')
        g = IB.gate_in(self.a, o, {'vehicle_number': 'MP04GA1000', 'challan_number': 'C1'}, {o.lines.get(): 50})
        gline = g.lines.get()
        IB.post(self.a, g.pk, {gline: {'received': D(50), 'accepted': D(50), 'rejected': D(0), 'damaged': D(0), 'hold': D(0), 'reason': ''}})
        self.assertEqual((bal('inventory_rm'), bal('grni')), (D('450000.00'), D('-450000.00')))
        gline.refresh_from_db()
        inv = AP.create_invoice(self.a, self.sup, {'invoice_number': 'S-1', 'invoice_date': TODAY(), 'supplier_gstin': self.sup.gstin, 'billed_taxable': D(450000),
                                                   'billed_tax': D(22500), 'billed_total': D(472500)}, {gline: (D(50), D(9000), D(5))})
        self.assertEqual((inv.status, bal('grni'), bal('ap', self.sup), bal('input_cgst') + bal('input_sgst')), ('matched', 0, D('-472500.00'), D('22500.00')))
        with self.assertRaisesMessage(ValidationError, 'Select at least one'):
            TR.propose(self.a, self.sup, self.bank, {})
        pay = TR.propose(self.a, self.sup, self.bank, {('sinv', inv.pk): D(472500)})
        with self.assertRaisesMessage(ValidationError, 'not open'):
            TR.propose(self.a, self.sup, self.bank, {('sinv', inv.pk): D(1)})  # already proposed in full
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            TR.decide(self.a, pay.pk, True)
        TR.decide(self.b, pay.pk, True)
        with self.assertRaisesMessage(ValidationError, 'UTR'):
            TR.release(self.b, pay.pk, '')
        TR.release(self.b, pay.pk, 'SBIN226112345')
        self.assertEqual((bal('ap', self.sup), bal('bank_main')), (0, D('-472500.00')))
        self.assertIn('Coal Co', TR.bank_file([SupplierPayment.objects.get(pk=pay.pk)]))
        TR.fail(self.b, pay.pk, 'Beneficiary account closed')
        self.assertEqual((bal('ap', self.sup), bal('bank_main')), (D('-472500.00'), 0))
        self.assertEqual(TR.open_items(self.sup)[0]['amount'], D('472500.00'))  # bill open again
        self.assert_balanced()
        self.assertEqual(gl.subledger_check()[1]['diff'], 0)  # inventory agrees with stock valuation

    def test_vouchers_periods_assets_bank_and_gst(self):
        cash = Account.objects.get(system_key='cash')
        power = Account.objects.get(system_key='power')
        with self.assertRaisesMessage(ValidationError, 'control account'):
            gl.create_voucher(self.a, TODAY(), None, 'x', [(Account.objects.get(system_key='ar'), D(1), 0, None, ''), (cash, 0, D(1), None, '')])
        with self.assertRaisesMessage(ValidationError, 'does not balance'):
            gl.create_voucher(self.a, TODAY(), None, 'Power', [(power, D(100), 0, None, ''), (cash, 0, D(90), None, '')])
        je = gl.create_voucher(self.a, TODAY(), self.site, 'Power bill provision', [(power, D(50000), 0, None, ''), (Account.objects.get(system_key='bank_main'), 0, D(50000), None, '')])
        self.assertEqual((je.status, bal('power')), ('draft', 0))  # drafts do not post
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            gl.decide_voucher(self.a, je.pk, True)
        last = TODAY().replace(day=1) - timedelta(days=1)
        from .closing import prepare_period
        prepare_period(self.a, last, 'Tax, stock and bank evidence reviewed', tax_reviewed=True, stock_reviewed=True, bank_reviewed=True)
        gl.set_period(self.b, last, True)
        with self.assertRaisesMessage(ValidationError, 'closed for posting'):
            gl.post(self.a, 'test:closed', last, None, 'x', [(power, D(1), 0, None, ''), (cash, 0, D(1), None, '')])
        gl.decide_voucher(self.b, je.pk, True)
        self.assertEqual(bal('power'), D('50000.00'))
        rev = gl.reverse(self.a, JournalEntry.objects.get(pk=je.pk), reason='Booked twice')
        self.assertEqual((bal('power'), rev.reversal_of_id), (0, je.pk))
        gl.set_period(self.b, last, False, 'Reopen for asset posting test')
        # Fixed asset: capitalise, depreciate once per month, dispose with loss
        a = TR.capitalise(self.a, {'code': 'vrm-1', 'name': 'VRM gearbox', 'category': 'fa_plant', 'site': self.site, 'supplier': self.sup, 'put_to_use': TODAY() - timedelta(days=70),
                                   'cost': D(1200000), 'salvage': D(0), 'method': 'slm', 'life_years': D(10)}, 'ap')
        month = TODAY().replace(day=1)
        self.assertEqual(TR.run_depreciation(self.b, month), (1, D('10000.00')))
        self.assertEqual(TR.run_depreciation(self.b, month), (0, D('0')))
        TR.dispose(self.b, a.pk, TODAY(), D(1000000), self.bank)
        self.assertEqual((bal('fa_plant'), bal('acc_depreciation'), bal('asset_loss')), (0, 0, D('190000.00')))
        # Bank reconciliation: import, duplicate skipped, auto-match on amount + UTR
        gl.post(self.a, 'test:dep', TODAY(), None, 'Customer NEFT', [(self.bank, D(25000), 0, None, 'UTR998'), (Account.objects.get(system_key='customer_advance'), 0, D(25000), None, '')])
        csv_text = f'Date,Narration,Ref,Withdrawal,Deposit,Balance\n{TODAY():%d/%m/%Y},NEFT IN DEALER,UTR998,,25000,25000\n{TODAY():%d/%m/%Y},BANK CHARGES,,59,,24941\n'
        self.assertEqual(TR.import_statement(self.a, self.bank, csv_text), (2, 0))
        self.assertEqual(TR.import_statement(self.a, self.bank, csv_text), (0, 2))
        self.assertEqual(TR.auto_match(self.a, self.bank), 1)
        rec = TR.reconciliation(self.bank, TODAY())
        self.assertEqual((len(rec['unmatched']), rec['statement']), (1, D('24941')))  # bank charge still to book
        g = TR.gst_summary(month, TODAY())
        self.assertEqual(g['net'], 0)
        self.assert_balanced()

    def test_screens_and_backfill(self):
        move_stock(self.a, self.site, self.cem, D(10), 'o1', 'Opening', unit_cost=D(280))
        JournalEntry.objects.all().delete()
        call_command('post_ledger', verbosity=0)
        self.assertTrue(JournalEntry.objects.filter(source__startswith='stk:').exists())
        call_command('post_ledger', verbosity=0)
        self.assertEqual(JournalEntry.objects.count(), 1)  # idempotent
        self.client.force_login(self.a)
        acc = Account.objects.get(system_key='inventory_fg')
        for url in ['journals', 'voucher_new', 'trial_balance', 'profit_loss', 'balance_sheet', 'chart', 'periods', 'payments', 'bank_rec', 'gst_summary', 'fixed_assets', 'cash_flow']:
            self.assertEqual(self.client.get(reverse(url)).status_code, 200, url)
        self.assertEqual(self.client.get(reverse('account_ledger', args=[acc.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('journal_detail', args=[JournalEntry.objects.get().pk])).status_code, 200)
        self.assertContains(self.client.get(reverse('trial_balance') + '?export=csv'), 'Finished goods')

    def test_supplier_rate_variance_clears_received_cost_not_invoice_rate(self):
        for idx, rate in enumerate([D(8000), D(10000)], 1):
            with self.subTest(rate=rate):
                order = Order.objects.create(kind='Purchase', site=self.site, partner=self.sup, created_by=self.a)
                OrderLine.objects.create(order=order, product=self.coal, quantity=50, rate=9000, gst_rate=5)
                transition_order(self.a, order.pk, 'submit')
                transition_order(self.b, order.pk, 'approve')
                grn = IB.gate_in(self.a, order, {'vehicle_number': 'MP04GA1000', 'challan_number': f'VAR-{idx}'}, {order.lines.get(): 50})
                line = grn.lines.get()
                IB.post(self.a, grn.pk, {line: {'received': D(50), 'accepted': D(50), 'rejected': D(0), 'damaged': D(0), 'hold': D(0), 'reason': ''}})
                line.refresh_from_db()
                taxable = rate * 50
                inv = AP.create_invoice(self.a, self.sup, dict(invoice_number=f'VAR-{idx}', invoice_date=TODAY(), supplier_gstin=self.sup.gstin,
                                        billed_taxable=taxable, billed_tax=taxable * D('.05'), billed_total=taxable * D('1.05')),
                                        {line: (D(50), rate, D(5))})
                if inv.status == 'exception':
                    AP.decide(self.b, inv.pk, 'billed', 'Approved revised commercial rate')
                self.assertEqual(bal('grni'), 0)
                entry = JournalEntry.objects.get(source=f'sinv:{inv.pk}')
                self.assertEqual(entry.lines.get(account__system_key='grni').debit, D(450000))
                self.assertTrue(all(not c['diff'] for c in gl.subledger_check()))
                self.assert_balanced()
