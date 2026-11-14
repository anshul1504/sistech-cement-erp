"""Acceptance cases for cutoff balances, independent close and year-end reversals."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import closing, gl, treasury as TR
from .models import Account, FinancialPeriod, FinancialYearClose, JournalEntry, Organization, Partner, Receipt, Site


@override_settings(EMAIL_OTP_ENABLED=False)
class ClosingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.maker = User.objects.create_superuser('close-maker', 'maker@example.com', 'Test-Password-816!')
        cls.checker = User.objects.create_superuser('close-checker', 'checker@example.com', 'Test-Password-816!')
        cls.viewer = User.objects.create_user('close-viewer')
        cls.viewer.user_permissions.add(*Permission.objects.filter(content_type__app_label='erp', codename__in=['access_company_finance', 'view_financials', 'view_financialyearclose']))
        cls.site = Site.objects.create(code='CLOSE', name='Closing plant', kind='Plant')
        cls.supplier = Partner.objects.create(code='CLOSE-S', name='Machine supplier', kind='Supplier', bank_account_number='1234567890', bank_ifsc='SBIN0000001')
        cls.customer = Partner.objects.create(code='CLOSE-D', name='Dealer', kind='Dealer')
        Organization.objects.update_or_create(pk=1, defaults={'fy_start_month': 4})
        gl.ensure_chart()
        cls.last = timezone.localdate().replace(day=1) - timedelta(days=1)

    def prepare(self, on=None, user=None):
        return closing.prepare_period(user or self.maker, on or self.last, 'Bank statement, stock count and tax review signed off',
                                      tax_reviewed=True, stock_reviewed=True, bank_reviewed=True)

    def cash_sale(self, on, amount=100, site=None, source='test:income'):
        return gl.post(self.maker, source, on, site, 'Cash revenue', [(gl.acct('cash'), D(amount), 0, None, ''), (gl.acct('sales_cement'), 0, D(amount), None, '')])

    def close_months(self, start, end):
        cursor = start
        while cursor <= end:
            self.prepare(cursor)
            gl.set_period(self.checker, cursor, True)
            cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)

    def previous_year(self):
        current_start, _ = closing.year_bounds(timezone.localdate())
        return closing.year_bounds(current_start - timedelta(days=1))

    def test_historical_receipt_does_not_disappear_after_later_bounce(self):
        receipt = Receipt.objects.create(number='H-RCT', partner=self.customer, date=self.last, amount=100, mode='cheque', created_by=self.maker)
        gl.post_receipt(self.maker, receipt)
        receipt.status, receipt.bounced_on = 'bounced', timezone.localdate()
        receipt.save()
        gl.reverse_source(self.checker, f'rct:{receipt.pk}', reason='Returned today')
        old = {c['name']: c for c in closing.historical_checks(self.last)}
        now = {c['name']: c for c in closing.historical_checks(timezone.localdate())}
        self.assertEqual(old['Trade receivables']['sub'], -100)
        self.assertEqual(now['Trade receivables']['sub'], 0)
        self.assertTrue(all(not c['diff'] for c in old.values()))
        self.assertTrue(all(not c['diff'] for c in now.values()))

    def test_historical_paid_asset_stays_paid_before_later_bank_return(self):
        asset = TR.capitalise(self.maker, dict(code='A-CLOSE', name='Machine', category='fa_plant', site=self.site, supplier=self.supplier,
            put_to_use=self.last - timedelta(days=31), cost=D(10000), salvage=D(0), method='slm', life_years=D(10)), 'ap')
        payment = TR.propose(self.maker, self.supplier, gl.acct('bank_main'), {('asset', asset.pk): D(10000)}, on=self.last)
        TR.decide(self.checker, payment.pk, True)
        TR.release(self.checker, payment.pk, 'UTR-HIST', self.last)
        TR.fail(self.checker, payment.pk, 'Returned in following month')
        old = {c['name']: c for c in closing.historical_checks(self.last)}
        now = {c['name']: c for c in closing.historical_checks(timezone.localdate())}
        self.assertEqual(old['Supplier payables']['sub'], 0)
        self.assertEqual(now['Supplier payables']['sub'], 10000)
        self.assertTrue(all(not c['diff'] for c in old.values()))
        self.assertTrue(all(not c['diff'] for c in now.values()))

    def test_close_requires_evidence_and_independent_checker(self):
        with self.assertRaisesMessage(ValidationError, 'fresh close review'):
            gl.set_period(self.checker, self.last, True)
        with self.assertRaises(ValidationError):
            closing.prepare_period(self.maker, self.last, 'Incomplete')
        self.prepare()
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            gl.set_period(self.maker, self.last, True)
        period = gl.set_period(self.checker, self.last, True)
        self.assertEqual(period.status, 'closed')
        self.assertEqual(period.reviews.get().approved_by, self.checker)

    def test_changed_balanced_books_invalidate_review(self):
        self.prepare()
        self.cash_sale(self.last)
        with self.assertRaisesMessage(ValidationError, 'changed after review'):
            gl.set_period(self.checker, self.last, True)
        self.prepare()
        self.assertEqual(gl.set_period(self.checker, self.last, True).status, 'closed')

    def test_missing_posting_is_a_close_blocker(self):
        Receipt.objects.create(number='MISSING', date=self.last, partner=self.customer, amount=100, created_by=self.maker)
        result = closing.checklist(gl.period_for(self.last))
        self.assertEqual(len(result['gaps']), 1)
        with self.assertRaisesMessage(ValidationError, 'missing ledger postings'):
            self.prepare()

    def test_bank_closing_confirmation_and_matches_are_required(self):
        bank = gl.acct('bank_main')
        gl.post(self.maker, 'test:bank', self.last, None, 'Bank capital', [(bank, D(1000), 0, None, 'UTR-CLOSE'), (gl.acct('capital'), 0, D(1000), None, '')])
        TR.import_statement(self.maker, bank, f'{self.last},Capital,UTR-CLOSE,,1000,1000\n')
        period = gl.period_for(self.last)
        with self.assertRaises(ValidationError):
            self.prepare()
        TR.auto_match(self.checker, bank)
        closing.confirm_bank(self.maker, period.pk, bank, D(900), 'Monthly statement ending at cutoff')
        with self.assertRaisesMessage(ValidationError, 'does not reconcile'):
            self.prepare()
        closing.confirm_bank(self.maker, period.pk, bank, D(1000), 'Monthly statement ending at cutoff')
        self.prepare()
        gl.set_period(self.checker, self.last, True)
        row = bank.statement_lines.get()
        with self.assertRaisesMessage(ValidationError, 'closed for posting'):
            TR.match(self.checker, row.pk, None)

    def test_cutoff_blocks_earlier_posting_and_review_history_survives_reopen(self):
        self.prepare()
        period = gl.set_period(self.checker, self.last, True)
        with self.assertRaisesMessage(ValidationError, 'closed for posting'):
            self.cash_sale(self.last.replace(day=1) - timedelta(days=1))
        with self.assertRaises(ValidationError):
            gl.set_period(self.checker, self.last, False)
        gl.set_period(self.checker, self.last, False, 'Additional adjustment required')
        self.assertEqual(period.reviews.count(), 1)
        with self.assertRaisesMessage(ValidationError, 'fresh close review'):
            gl.set_period(self.checker, self.last, True)
        self.prepare()
        gl.set_period(self.checker, self.last, True)
        self.assertEqual(period.reviews.count(), 2)

    def test_current_unfinished_month_cannot_be_closed(self):
        with self.assertRaisesMessage(ValidationError, 'month must finish'):
            self.prepare(timezone.localdate())

    def test_read_only_user_cannot_prepare_or_approve(self):
        with self.assertRaises(PermissionDenied):
            self.prepare(user=self.viewer)
        with self.assertRaises(PermissionDenied):
            gl.set_period(self.viewer, self.last, True)
        with self.assertRaises(PermissionDenied):
            closing.prepare_year(self.viewer, self.previous_year()[1], 'No write access')

    def test_year_close_carries_profit_by_site_and_preserves_pl(self):
        start, end = self.previous_year()
        self.cash_sale(start, amount=1000, site=self.site, source='test:site-income')
        self.cash_sale(start, amount=200, source='test:ho-income')
        gl.post(self.maker, 'test:expense', start, self.site, 'Power expense', [(gl.acct('power'), D(300), 0, None, ''), (gl.acct('cash'), 0, D(300), None, '')])
        self.close_months(start, end)
        row = closing.prepare_year(self.maker, end, 'Annual accounts and monthly reviews complete')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            closing.decide_year(self.maker, row.pk, True)
        closing.decide_year(self.checker, row.pk, True)
        self.assertEqual(row.entries.count(), 2)
        self.assertEqual(gl.profit_and_loss(start, end)['profit'], 900)
        self.assertEqual(gl.profit_and_loss(end + timedelta(days=1), timezone.localdate())['profit'], 0)
        self.assertEqual(gl.balance_sheet(end)['profit'], 0)
        self.assertEqual(gl.balance_sheet(end)['difference'], 0)
        for site in [self.site, None]:
            self.assertEqual(gl.balance_sheet(end, site)['difference'], 0)
        nominal = gl._posted(end=end).filter(account__type__in=['income', 'expense'])
        self.assertEqual(sum((l.debit - l.credit for l in nominal), D(0)), 0)
        with self.assertRaises(ValidationError):
            closing.decide_year(self.checker, row.pk, True)
        with self.assertRaisesMessage(ValidationError, 'year-end reopening'):
            gl.set_period(self.checker, end, False, 'Try bypassing year lock')
        with self.assertRaises(ValidationError):
            gl.reverse(self.checker, row.entries.first())

    def test_year_reopening_is_independent_reverses_and_can_be_reclosed_once(self):
        start, end = self.previous_year()
        self.cash_sale(start, 500)
        self.close_months(start, end)
        row = closing.prepare_year(self.maker, end, 'Annual review')
        closing.decide_year(self.checker, row.pk, True)
        closing.request_year_reopen(self.maker, row.pk, 'Correct annual classification')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            closing.approve_year_reopen(self.maker, row.pk, True)
        closing.approve_year_reopen(self.checker, row.pk, True)
        row.refresh_from_db()
        self.assertEqual(row.status, 'reopened')
        self.assertEqual(gl.balance_sheet(end)['profit'], 500)
        self.assertEqual(gl.balance_sheet(end)['difference'], 0)
        self.assertEqual(gl.profit_and_loss(start, end)['profit'], 500)
        self.assertEqual(row.entries.filter(reversal_of__isnull=False).count(), 1)
        with self.assertRaises(ValidationError):
            closing.approve_year_reopen(self.checker, row.pk, True)
        row = closing.prepare_year(self.maker, end, 'Revised annual review')
        self.assertEqual(row.revision, 2)
        closing.decide_year(self.checker, row.pk, True)
        self.assertEqual(gl.balance_sheet(end)['profit'], 0)
        self.assertEqual(row.history.filter(action='Closed').count(), 2)

    def test_year_close_requires_twelve_closed_months(self):
        with self.assertRaisesMessage(ValidationError, 'twelve months'):
            closing.prepare_year(self.maker, self.previous_year()[1], 'Unfinished')
        self.assertFalse(FinancialYearClose.objects.exists())

    def test_close_screens_and_source_drilldown(self):
        self.client.force_login(self.checker)
        period = gl.period_for(self.last)
        self.assertEqual(self.client.get(reverse('period_detail', args=[period.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('year_closes')).status_code, 200)
        receipt = Receipt.objects.create(number='DRILL', partner=self.customer, amount=10, created_by=self.maker)
        entry = gl.post_receipt(self.maker, receipt)
        self.assertContains(self.client.get(reverse('journal_detail', args=[entry.pk])), reverse('partner_ledger', args=[self.customer.pk]))
