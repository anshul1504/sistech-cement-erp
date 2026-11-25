"""Quarterly 26Q working file: quarter maths, deductee rows with challan, checks for missing PAN / undeposited TDS, CSV export."""
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from . import tds as T
from . import gl
from .models import Partner, TdsDeduction, TdsChallan, Account


@override_settings(EMAIL_OTP_ENABLED=False)
class TdsReturnTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('tds-admin', 't@example.test', 'x')
        gl.ensure_chart()
        self.bank = Account.objects.filter(is_bank=True).exclude(system_key='cash').first()
        self.with_pan = Partner.objects.create(code='TR-1', name='Roadways Co', kind='Transporter', pan='ABCDE1234F')
        self.no_pan = Partner.objects.create(code='TR-2', name='Small Truck Owner', kind='Transporter')

    def make(self, payee, amount, on, challan=None):
        from .models import ChannelRate, ChannelRun, ChannelRunLine
        rate = ChannelRate.objects.create(partner=payee, basis='pct', rate=1, valid_from=on, created_by=self.admin, status='approved')
        run = ChannelRun.objects.create(number=f'R{TdsDeduction.objects.count()}', start=on, end=on, created_by=self.admin, status='approved')
        line = ChannelRunLine.objects.create(run=run, partner=payee, rate=rate, amount=amount * 20, tds=amount, tds_section='194H')
        return TdsDeduction.objects.create(number=f'T{TdsDeduction.objects.count() + 1}', payee=payee, channel_line=line, section='194H', rate=5,
                                           base=amount * 20, amount=amount, date=on, challan=challan, created_by=self.admin)

    def test_quarter_rows_checks_and_csv(self):
        self.assertEqual(T.quarter_range(2026, 'Q4')[:2], (date(2027, 1, 1), date(2027, 3, 31)))
        ch = TdsChallan.objects.create(number='CH-1', section='194H', month=date(2026, 10, 1), amount=D('100'), bank=self.bank, challan_number='CIN0001',
                                       bsr_code='0510001', paid_on=date(2026, 11, 5), created_by=self.admin)
        self.make(self.with_pan, D('100'), date(2026, 10, 20), ch)
        self.make(self.no_pan, D('50'), date(2026, 11, 3))
        self.make(self.with_pan, D('70'), date(2026, 7, 3))              # previous quarter — not included
        data = T.return_data(2026, 'Q3')
        self.assertEqual(len(data['rows']), 2)
        self.assertEqual(data['total_tds'], D('150'))
        self.assertEqual(data['rows'][0]['code'], '02')
        self.assertTrue(any('no PAN' in c for c in data['checks']))
        self.assertTrue(any('no challan' in c for c in data['checks']))
        csv_text = T.return_csv(data)
        self.assertIn('ABCDE1234F', csv_text)
        self.assertIn('PANNOTAVBL', csv_text)
        self.assertIn('CIN0001', csv_text)
        self.client.force_login(self.admin)
        page = self.client.get(reverse('tds_return'), {'fy': 2026, 'q': 'Q3'})
        self.assertContains(page, 'Fix before filing')
        r = self.client.get(reverse('tds_return'), {'fy': 2026, 'q': 'Q3', 'export': 'csv'})
        self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn('26q-fy2026-27-Q3', r['Content-Disposition'])
