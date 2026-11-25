"""Sampling plan: validation, batch / period / truck due rules and the lab page."""
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import sampling as S
from .models import Production, QualitySpec, QualityResult, SamplingPlan
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class SamplingTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.spec = QualitySpec.objects.create(product=self.bag, parameter='Setting time', unit='min', min_value=30)
        self.batch = Production.objects.create(site=self.site, product=self.bag, planned_quantity=100, actual_quantity=100, batch='SMP-1', shift='A',
                                               status='Quality hold', created_by=self.maker, completed_at=timezone.now() - timedelta(hours=2))

    def test_rules_and_due(self):
        with self.assertRaisesMessage(ValidationError, 'incoming material'):
            S.save_plan(self.maker, self.bag, 'finished', 'truck')
        other = QualitySpec.objects.create(product=self.bulk, parameter='Fineness')
        with self.assertRaisesMessage(ValidationError, 'belong to the product'):
            S.save_plan(self.maker, self.bag, 'finished', 'batch', parameters=[other])
        S.save_plan(self.maker, self.bag, 'finished', 'batch', parameters=[self.spec])
        S.save_plan(self.maker, self.bag, 'dispatch', 'daily', site=self.site)
        due = S.due([self.site])
        self.assertEqual(len(due), 2)
        QualityResult.objects.create(production=self.batch, spec=self.spec, value=D(45), ok=True, analyst=self.maker)
        self.assertEqual(S.due([self.site]), [])
        self.client.force_login(self.checker)
        r = self.client.get(reverse('quality'))
        self.assertContains(r, 'Samples due')
        self.client.post(reverse('sampling_save'), {'product': self.bulk.pk, 'stage': 'incoming', 'frequency': 'truck'})
        self.assertTrue(SamplingPlan.objects.filter(product=self.bulk, frequency='truck').exists())
