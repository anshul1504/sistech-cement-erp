"""Collections desk: follow-up validation, promise kept / broken from receipts, disputes, worklist and screens."""
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import collect as C
from .models import CollectionFollowUp, Receipt
from .test_schemes import invoice_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class CollectionTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.inv = invoice_fixture(self, [(self.bag, D(10000))])
        self.inv.due_date = self.today - timedelta(days=20)
        self.inv.save()

    def test_promise_kept_and_broken(self):
        with self.assertRaisesMessage(ValidationError, 'promised amount'):
            C.record(self.maker, self.customer, 'promise', 'Will pay')
        with self.assertRaisesMessage(ValidationError, 'dispute reason'):
            C.record(self.maker, self.customer, 'dispute', 'Rate issue')
        p = C.record(self.maker, self.customer, 'promise', 'RTGS on Friday', D(5000), self.today + timedelta(days=2))
        self.assertEqual(C.promise_status(p), 'upcoming')
        Receipt.objects.create(number='QA-R1', date=self.today, partner=self.customer, amount=D(5000), created_by=self.maker)
        self.assertEqual(C.promise_status(p), 'kept')
        late = C.record(self.maker, self.customer, 'promise', 'Next week', D(9000), self.today)
        CollectionFollowUp.objects.filter(pk=late.pk).update(promised_date=self.today - timedelta(days=1), on=self.today - timedelta(days=5))
        late.refresh_from_db()
        self.assertEqual(C.promise_status(late, self.today), 'broken')  # today's receipt came after the promised date
        work = C.worklist()
        self.assertEqual(len(work['due']), 1)
        self.assertEqual(work['kept_pct'], 50)

    def test_dispute_untouched_and_screens(self):
        self.assertEqual([r['partner'].pk for r in C.worklist()['untouched']], [self.customer.pk])
        d = C.record(self.maker, self.customer, 'dispute', 'Says 20 bags short', dispute_reason='short', next_action_on=self.today)
        self.assertEqual(C.worklist()['untouched'], [])
        self.assertEqual(len(C.worklist()['disputes']), 1)
        with self.assertRaisesMessage(ValidationError, 'how it was settled'):
            C.resolve_dispute(self.maker, d.pk, '')
        self.client.force_login(self.checker)
        self.assertContains(self.client.get(reverse('collections')), 'Says 20 bags short')
        r = self.client.post(reverse('collections'), {'partner': self.customer.pk, 'kind': 'call', 'note': 'Owner travelling, call Monday',
                                                      'next_action_on': (self.today + timedelta(days=3)).isoformat()})
        self.assertEqual(r.status_code, 302)
        self.client.post(reverse('dispute_resolve', args=[d.pk]), {'resolution': 'Credit note for 20 bags'})
        self.assertTrue(CollectionFollowUp.objects.get(pk=d.pk).resolved)
        self.assertEqual(self.client.get(reverse('insight_area', args=['credit'])).status_code, 200)
