"""Excel report packs: content matches the KPI engine, permission-scoped packs, subscriptions and the scheduled email command."""
import io
from datetime import date
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from . import report_packs as RP
from .models import ReportSubscription, SiteAccess
from .test_schemes import invoice_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class ReportPackTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.inv = invoice_fixture(self, [(self.bag, D(12345))])

    def test_workbook_and_scope(self):
        name, data = RP.build(self.checker, 'management', RP.I.parse_period({'p': 'mtd'}))
        wb = load_workbook(io.BytesIO(data))
        self.assertIn('Sales summary', wb.sheetnames)
        self.assertIn('Ageing', wb.sheetnames)
        ws = wb['Sales summary']
        self.assertIn('Generated', ws['A2'].value)
        self.assertEqual(ws['B5'].value, 12345.0)
        rep = User.objects.create_user('qa-rep')
        rep.user_permissions.set(Permission.objects.filter(codename__in=['view_invoice']))
        SiteAccess.objects.create(user=rep, site=self.site)
        rep = User.objects.get(pk=rep.pk)
        self.assertTrue(RP.allowed(rep, 'sales'))
        self.assertFalse(RP.allowed(rep, 'credit'))
        self.assertIsNone(RP.build(rep, 'management', RP.I.parse_period({})))

    def test_schedule_and_screens(self):
        self.assertEqual(RP.period_for('monthly', date(2026, 12, 1)).start, date(2026, 11, 1))
        sub = ReportSubscription.objects.create(user=self.checker, pack='sales', frequency='weekly')
        self.assertEqual(RP.due(sub, date(2026, 11, 23)), True)    # Monday
        self.assertEqual(RP.due(sub, date(2026, 11, 24)), False)
        call_command('send_report_packs', '--force', stdout=io.StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertTrue(mail.outbox[0].attachments[0][0].endswith('.xlsx'))
        self.client.force_login(self.checker)
        self.assertEqual(self.client.get(reverse('report_packs')).status_code, 200)
        r = self.client.get(reverse('report_packs') + '?download=credit&p=ytd')
        self.assertEqual(r['Content-Type'], 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        r = self.client.get(reverse('report_packs') + '?download=management&format=pdf&p=ytd')
        self.assertTrue(r.content.startswith(b'%PDF'))
        self.assertContains(self.client.get(reverse('insight_area', args=['sales'])), 'format=pdf')
        self.client.post(reverse('report_packs'), {'pack': 'stock', 'frequency': 'daily'})
        self.assertTrue(ReportSubscription.objects.filter(user=self.checker, pack='stock', frequency='daily').exists())
        nobody = User.objects.create_user('qa-nobody')
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse('report_packs')).status_code, 403)
