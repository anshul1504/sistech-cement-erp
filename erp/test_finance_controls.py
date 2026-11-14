"""Regression cases for finance authorization and money movement boundaries."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import gl, treasury as TR, payables as AP
from .models import Account, BankStatementLine, FinancialPeriod, JournalEntry, Partner, Site


@override_settings(EMAIL_OTP_ENABLED=False)
class FinanceControlsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maker = User.objects.create_superuser('finance-maker', 'maker@example.com', 'Test-Password-816!')
        cls.checker = User.objects.create_superuser('finance-checker', 'checker@example.com', 'Test-Password-816!')
        cls.viewer = User.objects.create_user('finance-viewer')
        cls.viewer.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=[
            'view_financials', 'view_journalentry', 'view_supplierpayment', 'view_bankstatementline', 'view_fixedasset', 'view_account']))
        cls.site = Site.objects.create(code='FIN', name='Finance plant', kind='Plant')
        cls.supplier = Partner.objects.create(code='FIN-S', name='Asset supplier', kind='Supplier', bank_account_number='1234567890', bank_ifsc='SBIN0000001')
        gl.ensure_chart()
        cls.bank = gl.acct('bank_main')

    def asset(self, **changes):
        data = dict(code='FA-1', name='Mill', category='fa_plant', site=self.site, supplier=self.supplier,
                    put_to_use=timezone.localdate() - timedelta(days=90), cost=D('120000'), salvage=D(0), method='slm', life_years=D(10))
        data.update(changes)
        return TR.capitalise(self.maker, data, 'ap')

    def deposit(self, amount=100, reference='UTR001', on=None):
        return gl.post(self.maker, reference, on or timezone.localdate(), None, reference,
                       [(self.bank, D(amount), 0, None, reference), (gl.acct('capital'), 0, D(amount), None, '')])

    def test_module_permissions_do_not_grant_company_books(self):
        self.client.force_login(self.viewer)
        for route in ['journals', 'trial_balance', 'profit_loss', 'balance_sheet', 'payments', 'bank_rec', 'fixed_assets', 'cash_flow', 'gst_summary', 'periods', 'chart']:
            self.assertEqual(self.client.get(reverse(route)).status_code, 403, route)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'href="/accounts-ledger/"')
        self.viewer.user_permissions.add(Permission.objects.get(codename='access_company_finance'))
        self.assertEqual(self.client.get(reverse('trial_balance')).status_code, 200)

    def test_read_only_bank_user_cannot_auto_match(self):
        self.viewer.user_permissions.add(Permission.objects.get(codename='access_company_finance'))
        with self.assertRaises(PermissionDenied):
            TR.auto_match(self.viewer, self.bank)

    def test_credit_asset_payment_reconciles_and_failure_reopens_it(self):
        asset = self.asset()
        self.assertEqual(AP.balance(self.supplier)['balance'], asset.cost)
        payment = TR.propose(self.maker, self.supplier, self.bank, {('asset', asset.pk): asset.cost})
        self.assertFalse(TR.open_items(self.supplier))
        TR.decide(self.checker, payment.pk, True)
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            TR.release(self.maker, payment.pk, 'UTR001')
        TR.release(self.checker, payment.pk, 'UTR001')
        self.assertEqual(AP.balance(self.supplier)['balance'], 0)
        self.assertTrue(all(not c['diff'] for c in gl.subledger_check()))
        TR.fail(self.checker, payment.pk, 'Bank returned funds')
        self.assertEqual(AP.balance(self.supplier)['balance'], asset.cost)
        self.assertEqual(TR.open_items(self.supplier)[0]['amount'], asset.cost)
        self.assertTrue(all(not c['diff'] for c in gl.subledger_check()))

    def test_changed_beneficiary_blocks_release_and_preserves_snapshot(self):
        asset = self.asset()
        payment = TR.propose(self.maker, self.supplier, self.bank, {('asset', asset.pk): asset.cost})
        TR.decide(self.checker, payment.pk, True)
        self.supplier.bank_account_number = '9876543210'
        self.supplier.save()
        with self.assertRaisesMessage(ValidationError, 'Beneficiary details changed'):
            TR.release(self.checker, payment.pk, 'UTR002')
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'approved')
        self.assertEqual(payment.beneficiary_account, '1234567890')
        with self.assertRaisesMessage(ValidationError, 'beneficiary details changed'):
            TR.bank_file(type(payment).objects.filter(pk=payment.pk).select_related('payee'))
        TR.decide(self.checker, payment.pk, False, 'Bank account updated; request fresh approval')
        self.assertEqual(TR.open_items(self.supplier)[0]['amount'], asset.cost)

    def test_invalid_payment_amount_mode_and_bank_are_rejected(self):
        asset = self.asset()
        for amount in ['NaN', 'Infinity', '-1', '120001']:
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                TR.propose(self.maker, self.supplier, self.bank, {('asset', asset.pk): D(amount)})
        with self.assertRaises(ValidationError):
            TR.propose(self.maker, self.supplier, self.bank, {('asset', asset.pk): D(1)}, mode='wrong')
        with self.assertRaises(ValidationError):
            TR.propose(self.maker, self.supplier, gl.acct('cash'), {('asset', asset.pk): D(1)})

    def test_bank_import_rejects_negative_and_nonfinite_amounts_atomically(self):
        for bad in ['-10', 'NaN', 'Infinity']:
            text = f'{timezone.localdate()},Valid,REF1,,100,100\n{timezone.localdate()},Invalid,REF2,,{bad},100\n'
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                TR.import_statement(self.maker, self.bank, text)
            self.assertFalse(BankStatementLine.objects.exists())

    def test_auto_match_needs_unique_exact_reference(self):
        self.deposit(reference='UTR001')
        self.deposit(reference='UTR001-OTHER')
        today = timezone.localdate()
        TR.import_statement(self.maker, self.bank, f'{today},Deposit,UTR001,,100,100\n{today},Unknown,UNKNOWN,,100,200\n')
        self.assertEqual(TR.auto_match(self.maker, self.bank), 1)
        self.assertEqual(BankStatementLine.objects.get(reference='UTR001').matched_line.entry.source, 'UTR001')
        self.assertIsNone(BankStatementLine.objects.get(reference='UNKNOWN').matched_line)

    def test_bank_match_and_unmatch_are_audited_and_validate_bank(self):
        entry = self.deposit()
        today = timezone.localdate()
        TR.import_statement(self.maker, self.bank, f'{today},Deposit,UTR001,,100,100\n')
        row = BankStatementLine.objects.get()
        with self.assertRaises(ValidationError):
            TR.match(self.checker, row.pk, entry.lines.get(account=gl.acct('capital')).pk)
        TR.match(self.checker, row.pk, entry.lines.get(account=self.bank).pk)
        TR.match(self.checker, row.pk, None)
        from .models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(action='Bank line matched').exists())
        self.assertTrue(AuditEvent.objects.filter(action='Bank match removed').exists())

    def test_historical_reconciliation_keeps_later_bank_match_outstanding(self):
        today = timezone.localdate()
        entry = self.deposit(on=today - timedelta(days=2))
        TR.import_statement(self.maker, self.bank, f'{today},Deposit,UTR001,,100,100\n')
        TR.auto_match(self.checker, self.bank)
        rec = TR.reconciliation(self.bank, today - timedelta(days=1))
        self.assertEqual(list(rec['unpresented']), [entry.lines.get(account=self.bank)])

    def test_period_button_posts_close_and_unknown_action_is_404(self):
        self.client.force_login(self.checker)
        page = self.client.get(reverse('periods'))
        period = FinancialPeriod.objects.get(start=timezone.localdate().replace(day=1))
        self.assertContains(page, reverse('period_action', args=[period.pk, 'close']))
        self.client.post(reverse('period_action', args=[period.pk, 'close']))
        period.refresh_from_db()
        self.assertEqual(period.status, 'closed')
        self.assertEqual(self.client.post(reverse('period_action', args=[period.pk, 'unknown'])).status_code, 404)

    def test_unmatched_statement_prevents_period_close(self):
        today = timezone.localdate()
        TR.import_statement(self.maker, self.bank, f'{today},Deposit,UNMATCHED,,100,100\n')
        with self.assertRaisesMessage(ValidationError, 'outstanding bank statement'):
            gl.set_period(self.checker, today, True)

    def test_system_posting_cannot_be_reversed_from_journal_screen(self):
        entry = self.deposit()
        self.client.force_login(self.checker)
        self.assertNotContains(self.client.get(reverse('journal_detail', args=[entry.pk])), 'Reverse this voucher')
        self.client.post(reverse('journal_action', args=[entry.pk, 'reverse']), {'reason': 'No'})
        self.assertFalse(JournalEntry.objects.filter(reversal_of=entry).exists())

    def test_asset_policies_and_month_end_boundary(self):
        for changes in [dict(method='bad'), dict(life_years=D(-2)), dict(method='wdv', wdv_rate=D(101)), dict(cost=D('NaN'))]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.asset(**changes)
        asset = self.asset(put_to_use=date(2026, 1, 31))
        self.assertEqual(TR.monthly_depreciation(asset, date(2026, 1, 1)), D(1000))
        with self.assertRaisesMessage(ValidationError, 'predate'):
            TR.dispose(self.checker, asset.pk, date(2026, 1, 30), D(0), None)

    def test_report_default_follows_company_financial_year(self):
        from .accounts_views import _range
        from .models import Organization
        from django.test import RequestFactory
        Organization.objects.update_or_create(pk=1, defaults={'fy_start_month': 1})
        start, end = _range(RequestFactory().get('/'))
        self.assertEqual(start, date(timezone.localdate().year, 1, 1))
        self.assertEqual(end, timezone.localdate())

    def test_payment_cannot_ignore_credit_and_exceed_net_open_balance(self):
        from .models import DebitNote
        asset = self.asset()
        credit = DebitNote.objects.create(number='DN-FIN', supplier=self.supplier, site=self.site, date=timezone.localdate(), reason='quality',
                                         remarks='Quality allowance', taxable=D(5000), tax=0, total=D(5000), created_by=self.maker)
        gl.post_debit_note(self.maker, credit)
        with self.assertRaisesMessage(ValidationError, 'net open balance'):
            TR.propose(self.maker, self.supplier, self.bank, {('asset', asset.pk): asset.cost})
        payment = TR.propose(self.maker, self.supplier, self.bank, {('asset', asset.pk): asset.cost, ('dn', credit.pk): D(5000)})
        self.assertEqual(payment.amount, D(115000))
