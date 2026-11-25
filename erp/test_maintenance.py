"""Maintenance: job lifecycle with independent verification, preventive schedules, KPIs, screens."""
from datetime import timedelta

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import maintenance as M
from .models import Equipment, MaintenancePlan, MaintenanceJob
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class MaintenanceTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.eq = Equipment.objects.create(site=self.site, code='PKR-1', name='Packer 1', kind='packer', criticality='high')

    def test_breakdown_cycle_and_kpis(self):
        job = M.report(self.maker, self.eq, 'breakdown', 'Spout jamming')
        with self.assertRaisesMessage(ValidationError, 'Start the job'):
            M.finish(self.maker, job.pk, 'x')
        M.start(self.maker, job.pk, self.maker)
        with self.assertRaisesMessage(ValidationError, 'work done'):
            M.finish(self.maker, job.pk, '')
        M.finish(self.maker, job.pk, 'Replaced spout bush', 'Bush x2', 45)
        with self.assertRaisesMessage(ValidationError, 'own work'):
            M.verify(self.maker, job.pk, True)
        M.verify(self.checker, job.pk, False, 'Still leaking')
        self.assertEqual(MaintenanceJob.objects.get(pk=job.pk).status, 'progress')
        M.finish(self.maker, job.pk, 'Seal replaced', '', 30)
        M.verify(self.checker, job.pk, True)
        k = M.kpis([self.site])
        self.assertEqual((k['open'], k['verify'], k['downtime_h']), (0, 0, 0.5))
        self.assertIsNotNone(k['mttr'])

    def test_preventive_schedule_and_screen(self):
        plan = MaintenancePlan.objects.create(equipment=self.eq, task='Gearbox oil', every_days=30, next_due=self.today)
        made = M.open_due(self.maker, [self.site])
        self.assertEqual(len(made), 1)
        self.assertEqual(M.open_due(self.maker, [self.site]), [])           # one open job per plan
        job = made[0]
        M.start(self.maker, job.pk); M.finish(self.maker, job.pk, 'Oil changed'); M.verify(self.checker, job.pk, True)
        self.assertEqual(MaintenancePlan.objects.get(pk=plan.pk).next_due, self.today + timedelta(days=30))
        self.client.force_login(self.checker)
        self.assertEqual(self.client.get(reverse('maintenance') + '?status=all').status_code, 200)
        self.client.post(reverse('maintenance_action', args=['report']), {'equipment': self.eq.pk, 'kind': 'breakdown', 'priority': 'urgent', 'description': 'Motor tripping'})
        self.assertTrue(MaintenanceJob.objects.filter(description='Motor tripping').exists())
        self.client.post(reverse('maintenance_action', args=['equipment']), {'site': self.site.pk, 'code': 'kln-1', 'name': 'Kiln', 'kind': 'kiln'})
        self.assertTrue(Equipment.objects.filter(code='KLN-1').exists())
        nobody = User.objects.create_user('qa-none')
        with self.assertRaises(Exception):
            M.report(nobody, self.eq, 'breakdown', 'x')
