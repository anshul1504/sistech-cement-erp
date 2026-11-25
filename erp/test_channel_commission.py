"""Channel commission: rates with maker-checker, network-based runs, GL + TDS posting, payable in Payments, recovery after returns."""
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from . import channel_commission as C
from . import network as net
from . import gl
from .models import Partner, JournalEntry, TdsDeduction, InvoiceLine, ChannelRunLine, Account
from .test_projects import seed_projects
from .test_schemes import invoice_fixture, credit_fixture


@override_settings(EMAIL_OTP_ENABLED=False)
class ChannelCommissionTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.customer.kind = 'Dealer'
        self.customer.save()
        self.a = User.objects.create_superuser('ch-maker', 'a@example.test', 'x')
        self.b = User.objects.create_superuser('ch-checker', 'b@example.test', 'x')
        self.cf = Partner.objects.create(code='CF-QA', name='QA C&F', kind='CF', tds_section='194H', tds_rate=D('5'),
                                         bank_account_number='123456789012', bank_ifsc='SBIN0000001')
        net.set_parent(self.a, self.customer, self.cf)
        self.inv = invoice_fixture(self, [(self.bag, D('1000'))])

    def rate(self, **kw):
        data = dict(partner=self.cf, basis='pct', rate=D('2'), scope='network', valid_from=self.today.replace(day=1), valid_to=None, note='QA agreement')
        data.update(kw)
        r = C.save_rate(self.a, data)
        C.submit_rate(self.a, r.pk)
        return r

    def test_rate_needs_a_different_approver_and_no_overlap(self):
        r = self.rate()
        with self.assertRaises(ValidationError):
            C.decide_rate(self.a, r.pk, True)
        C.decide_rate(self.b, r.pk, True)
        with self.assertRaises(ValidationError):
            self.rate(rate=D('3'))                       # second rate for the same dates
        with self.assertRaises(ValidationError):
            C.save_rate(self.a, dict(partner=self.customer, basis='pct', rate=D('1'), scope='network', valid_from=self.today))  # a dealer is not a channel partner

    def test_run_posts_payable_and_tds_then_recovers_after_return(self):
        C.decide_rate(self.b, self.rate().pk, True)
        run = C.calculate(self.a, self.today)
        line = run.lines.get()
        self.assertEqual((line.base_value, line.amount, line.tds, line.net), (D('1000.00'), D('20.00'), D('1'), D('19.00')))
        with self.assertRaises(ValidationError):
            C.decide_run(self.a, run.pk, True)           # maker-checker
        C.decide_run(self.b, run.pk, True)
        entry = JournalEntry.objects.get(source=f'chc:{run.pk}')
        by = {(l.account.system_key, l.partner_id): (l.debit, l.credit) for l in entry.lines.select_related('account')}
        self.assertEqual(by[('channel_commission', None)], (D('20.00'), D('0')))
        self.assertEqual(by[('ap', self.cf.pk)], (D('0'), D('19.00')))
        self.assertEqual(by[('tds_payable', None)], (D('0'), D('1.00')))
        tds = TdsDeduction.objects.get(channel_line=line)
        self.assertEqual((tds.section, tds.amount), ('194H', D('1')))
        from .treasury import open_items, propose
        items = [i for i in open_items(self.cf) if i['kind'] == 'chc']
        self.assertEqual(items[0]['amount'], D('19.00'))
        gl.ensure_chart()
        bank = Account.objects.filter(is_bank=True).exclude(system_key='cash').first()
        pay = propose(self.a, self.cf, bank, {('chc', line.pk): D('19')}, mode='neft')
        self.assertEqual(pay.allocations.get().channel_line, line)
        with self.assertRaises(ValidationError):
            C.calculate(self.a, self.today)               # nothing new since the approved run

    def test_recovery_line_when_returns_exceed_new_sales(self):
        C.decide_rate(self.b, self.rate(valid_from=self.today.replace(day=1)).pk, True)
        first = C.calculate(self.a, self.today)
        C.decide_run(self.b, first.pk, True)
        # Next run must start after the first one; move the first run back a day so today's return can be counted.
        from datetime import timedelta
        first.end = self.today - timedelta(days=1)
        first.save(update_fields=['end'])
        credit_fixture(self, self.inv, 500, line=InvoiceLine.objects.filter(invoice=self.inv).first())
        second = C.calculate(self.a, self.today)
        line = second.lines.get()
        self.assertEqual(line.amount, D('-10.00'))        # 2 % of the 500 credit note
        self.assertEqual(line.tds, D('0'))
        C.decide_run(self.b, second.pk, True)
        entry = JournalEntry.objects.get(source=f'chc:{second.pk}')
        self.assertTrue(entry.lines.filter(account__system_key='ap', partner=self.cf, debit=D('10.00')).exists())

    def test_screens_and_permissions(self):
        self.client.force_login(self.a)
        self.assertContains(self.client.get(reverse('channel_commission')), 'Channel commission')
        r = self.client.post(reverse('channel_commission'), {'partner': self.cf.pk, 'basis': 'mt', 'rate': '25', 'scope': 'network',
                                                             'valid_from': str(self.today.replace(day=1)), 'submit': '1'}, follow=True)
        self.assertContains(r, 'sent for approval')
        self.assertContains(self.client.get(reverse('partner_detail', args=[self.cf.pk])), 'No approved rate')
        viewer = User.objects.create_user('ch-viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('channel_commission')).status_code, 403)
        self.assertEqual(ChannelRunLine.objects.count(), 0)
