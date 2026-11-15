"""Cross-site web and domain-service regressions using disposable business records."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import complaints as CM, quotes as QT
from .models import AuditEvent, Complaint, Quotation
from .test_schemes import invoice_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class AccessHardeningTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.invoice = invoice_fixture(self, [(self.bag, 100)])
        self.case = CM.log(self.maker, self.customer, 'quality', 'Damaged bags', self.invoice)
        today = timezone.localdate()
        self.data = dict(site=self.site, partner=self.customer, lead=None, date=today,
                         valid_until=today + timedelta(days=10), place_of_supply='23',
                         freight_terms='for_destination', delivery_terms='', payment_terms='', notes='')
        self.quote = QT.save(self.maker, self.data, [(self.bag, 100, 380)])

    def test_quote_service_rejects_every_foreign_transition_without_writes(self):
        before = AuditEvent.objects.count()
        calls = [
            lambda: QT.save(self.outsider, self.data, [(self.bag, 100, 380)]),
            lambda: QT.save(self.outsider, dict(self.data, site=self.other), [(self.bag, 100, 380)], self.quote),
            lambda: QT.submit(self.outsider, self.quote.pk),
            lambda: QT.approve(self.outsider, self.quote.pk, True),
            lambda: QT.mark_sent(self.outsider, self.quote.pk),
            lambda: QT.outcome(self.outsider, self.quote.pk, True),
            lambda: QT.revise(self.outsider, self.quote.pk),
            lambda: QT.convert(self.outsider, self.quote.pk),
        ]
        for call in calls:
            with self.assertRaises(PermissionDenied):
                call()
        self.assertEqual(Quotation.objects.count(), 1)
        self.quote.refresh_from_db()
        self.assertEqual(self.quote.status, 'draft')
        self.assertEqual(AuditEvent.objects.count(), before)

    def test_complaint_web_and_services_reject_foreign_site(self):
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('complaints'))
        self.assertNotContains(response, self.case.number)
        self.assertEqual(self.client.get(reverse('complaint_detail', args=[self.case.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('complaint_action', args=[self.case.pk, 'investigate']),
                                          {'findings': 'Forbidden update'}).status_code, 404)
        for call in [
            lambda: CM.log(self.outsider, self.customer, 'quality', 'Foreign invoice', self.invoice),
            lambda: CM.investigate(self.outsider, self.case.pk, 'Forbidden'),
            lambda: CM.decide(self.outsider, self.case.pk, 'reject', 0, 'none', 'Forbidden'),
            lambda: CM.settle(self.outsider, self.case.pk),
            lambda: CM.close(self.outsider, self.case.pk),
            lambda: CM.add_capa(self.outsider, self.case, 'Test', 'Cause', 'Action', self.outsider,
                               timezone.localdate() + timedelta(days=1)),
        ]:
            with self.assertRaises(PermissionDenied):
                call()
        self.case.refresh_from_db()
        self.assertEqual(self.case.status, 'open')
        self.assertEqual(Complaint.objects.count(), 1)

    def test_capa_owner_cannot_bypass_site_or_substitute_another_case(self):
        capa = CM.add_capa(self.maker, self.case, 'Storage', 'Wet floor', 'Use pallets', self.outsider,
                           timezone.localdate() + timedelta(days=1))
        with self.assertRaises(PermissionDenied):
            CM.implement(self.outsider, capa.pk, 'Forbidden')
        with self.assertRaises(PermissionDenied):
            CM.verify(self.outsider, capa.pk, True, 'Forbidden')
        second = CM.log(self.maker, self.customer, 'quality', 'Second case', self.invoice)
        self.client.force_login(self.maker)
        response = self.client.post(reverse('complaint_action', args=[second.pk, 'implement']),
                                    {'capa': capa.pk, 'note': 'Wrong parent'})
        self.assertEqual(response.status_code, 404)
        capa.refresh_from_db()
        self.assertEqual(capa.status, 'open')

    def test_unlinked_cases_are_private_and_assigned_site_still_works(self):
        case = CM.log(self.maker, self.customer, 'other', 'Private case')
        self.assertFalse(CM.scope(Complaint.objects.all(), self.outsider).filter(pk=case.pk).exists())
        CM.investigate(self.maker, case.pk, 'Owner investigation')
        CM.investigate(self.checker, self.case.pk, 'Assigned plant investigation')
        self.assertEqual(QT.submit(self.maker, self.quote.pk).status, 'ready')

    def test_reports_and_work_queue_do_not_leak_cases_or_counts(self):
        from . import insights as I
        CM.investigate(self.checker, self.case.pk, 'Investigated')
        private = CM.log(self.maker, self.customer, 'other', 'Private report case')
        self.client.force_login(self.outsider)
        response = self.client.get(reverse('insight_drill', args=['complaints']) + '?p=mtd&export=csv')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.case.number)
        self.assertNotContains(response, private.number)
        queue = I.my_queue(self.outsider)
        self.assertFalse(any(row['label'] == 'Complaints to decide' for row in queue))
        quality = I.quality_summary([self.other], I.parse_period({'p': 'mtd'}), user=self.outsider)
        self.assertEqual(quality['complaints_new'], 0)
        self.client.get(reverse('complaint_new') + f'?partner={self.customer.pk}&invoice={self.invoice.pk}')
        response = self.client.post(reverse('complaint_new'), {
            'save': '1', 'partner': self.customer.pk, 'invoice': self.invoice.pk,
            'category': 'other', 'description': 'Forged invoice',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Complaint.objects.count(), 2)
