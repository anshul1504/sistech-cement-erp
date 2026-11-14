"""Complaints and CAPA: links, SLA, independent decision, settlement evidence, CAPA verification and closure rules."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import complaints as CM
from .models import Partner, CreditNote, Complaint
from .test_schemes import invoice_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class ComplaintTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.qm = User.objects.create_superuser('cm-qm', 'q@example.com', 'Verification-Password-816!')
        self.inv = invoice_fixture(self, [(self.bag, D(1000))])

    def test_quality_complaint_full_cycle(self):
        other = Partner.objects.create(code='X1', name='Other dealer', kind='Dealer', credit_limit=0)
        with self.assertRaisesMessage(ValidationError, 'is not for'):
            CM.log(self.maker, other, 'quality', 'Lumps', self.inv)
        c = CM.log(self.maker, self.customer, 'quality', 'Hard lumps in 40 bags', self.inv, product=self.bag, quantity=40)
        self.assertEqual(c.due_on, self.today + timedelta(days=7))
        with self.assertRaisesMessage(ValidationError, 'findings first'):
            CM.decide(self.qm, c.pk, 'accept', 40, 'credit', 'ok')
        CM.investigate(self.checker, c.pk, 'Site visit: bags stored on wet floor for 2 weeks, 25 bags hardened')
        with self.assertRaisesMessage(ValidationError, 'cannot decide'):
            CM.decide(self.checker, c.pk, 'partial', 25, 'credit', 'x')
        with self.assertRaisesMessage(ValidationError, 'exceed'):
            CM.decide(self.qm, c.pk, 'partial', 50, 'credit', 'x')
        CM.decide(self.qm, c.pk, 'partial', 25, 'credit', 'Goodwill credit for 25 bags')
        with self.assertRaises(ValidationError) as e:
            CM.close(self.qm, c.pk)
        self.assertTrue(any('credit note' in m for m in e.exception.messages) and any('CAPA' in m for m in e.exception.messages))
        cn = CreditNote.objects.create(number='CMP-CN', invoice=self.inv, partner=self.customer, reason='quality', taxable=500, cgst=45, sgst=45, igst=0, round_off=0, total=590, created_by=self.maker)
        CM.settle(self.maker, c.pk, credit_note=cn)
        x = CM.add_capa(self.qm, c, 'Dealer storage advice', 'Stored on wet floor', 'Pallets and storage leaflet for dealers', self.checker, self.today + timedelta(days=10))
        CM.implement(self.checker, x.pk, 'Leaflets distributed, 20 dealers visited')
        with self.assertRaisesMessage(ValidationError, 'owner cannot verify'):
            CM.verify(self.checker, x.pk, True, 'ok')
        CM.verify(self.qm, x.pk, True, 'No repeat complaints in the region for 6 weeks')
        CM.close(self.qm, c.pk)
        self.assertEqual(Complaint.objects.get(pk=c.pk).status, 'closed')

    def test_rejected_and_waived_closure_and_screens(self):
        c = CM.log(self.maker, self.customer, 'short', 'Claimed 10 bags short', self.inv, quantity=10)
        self.assertEqual(c.due_on, self.today + timedelta(days=3))
        CM.investigate(self.checker, c.pk, 'Weighbridge slip and POD show full delivery')
        CM.decide(self.qm, c.pk, 'reject', 99, '', 'POD signed without remark')
        CM.close(self.qm, c.pk)
        q = CM.log(self.maker, self.customer, 'quality', 'Colour variation', self.inv)
        CM.investigate(self.checker, q.pk, 'Within specification; natural fly-ash variation')
        CM.decide(self.qm, q.pk, 'accept', 0, 'none', 'Explained to customer')
        with self.assertRaisesMessage(ValidationError, 'record why'):
            CM.close(self.qm, q.pk)
        CM.close(self.qm, q.pk, 'Material within IS 1489; no process cause')
        self.client.force_login(self.qm)
        for url in [reverse('complaints'), reverse('complaints') + '?status=closed', reverse('complaint_new') + f'?partner={self.customer.pk}&invoice={self.inv.pk}',
                    reverse('complaint_detail', args=[q.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
