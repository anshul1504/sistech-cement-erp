from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import production as PR
from . import quality as QC
from .logistics import batches_for
from .models import (Site, Product, StockBalance, StockMovement, Production, Recipe, QualitySpec, LabEquipment, ShiftLog, AuditEvent, Organization)
from .services import move_stock, transition_production

D = Decimal


def bal(site, product):
    return StockBalance.objects.filter(site=site, product=product, status='available').values_list('quantity', flat=True).first() or D('0')


@override_settings(EMAIL_OTP_ENABLED=False)
class ProductionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Organization.objects.update_or_create(pk=1, defaults={'state': '23'})
        cls.site = Site.objects.create(code='PLT', name='Plant', kind='Plant', state='23')
        cls.opc = Product.objects.create(code='OPC-BULK', name='OPC 53 bulk', category='Cement', unit='MT', grade='OPC 53')
        cls.bag = Product.objects.create(code='OPC-50', name='OPC 53 · 50 kg', category='Cement', unit='BAG', bag_weight_kg=50, grade='OPC 53')
        cls.clk = Product.objects.create(code='CLK', name='Clinker', category='Raw material', unit='MT')
        cls.gyp = Product.objects.create(code='GYP', name='Gypsum', category='Raw material', unit='MT')
        cls.sack = Product.objects.create(code='HDPE', name='Empty HDPE bag', category='Packaging', unit='PCS')
        cls.a = User.objects.create_superuser('pr-a', 'a@example.com', 'Original-Password-816!')
        cls.b = User.objects.create_superuser('pr-b', 'b@example.com', 'Original-Password-816!')
        cls.qm = User.objects.create_superuser('pr-qm', 'q@example.com', 'Original-Password-816!')
        move_stock(cls.a, cls.site, cls.clk, D(500), 'o-clk', 'Opening', unit_cost=D(3000))
        move_stock(cls.a, cls.site, cls.gyp, D(50), 'o-gyp', 'Opening', unit_cost=D(1500))
        move_stock(cls.a, cls.site, cls.sack, D(5000), 'o-sack', 'Opening', unit_cost=D(8))

    def recipe(self):
        r = PR.save_recipe(self.a, self.opc, [(self.clk, D('0.95'), 2), (self.gyp, D('0.05'), 5)])
        PR.approve_recipe(self.b, r.pk)
        return r

    def run_batch(self, out=100):
        p = PR.create_order(self.a, self.site, self.opc, 100, 'A', 'Cement mill 1')
        PR.issue(self.a, p.pk, self.clk, 95, 'Clinker silo 1')
        PR.issue(self.a, p.pk, self.gyp, 5, 'Gypsum shed')
        return PR.complete(self.a, p.pk, out)

    def test_recipe_versions_and_costed_completion(self):
        r1 = PR.save_recipe(self.a, self.opc, [(self.clk, D('0.96'), 2), (self.gyp, D('0.04'), 5)])
        with self.assertRaisesMessage(ValidationError, 'no approved version'):
            PR.create_order(self.a, self.site, self.opc, 100, 'A')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            PR.approve_recipe(self.a, r1.pk)
        PR.approve_recipe(self.b, r1.pk)
        old = PR.create_order(self.a, self.site, self.opc, 100, 'A')
        r2 = self.recipe()
        self.assertEqual((Recipe.objects.get(pk=r1.pk).status, r2.version, Production.objects.get(pk=old.pk).recipe_id), ('retired', 2, r1.pk))
        p = PR.create_order(self.a, self.site, self.opc, 100, 'B', 'Cement mill 1')
        self.assertEqual((p.recipe, p.batch[:4]), (r2, 'PLT-'))
        with self.assertRaisesMessage(ValidationError, 'Issue the materials'):
            PR.complete(self.a, p.pk, 100)
        with self.assertRaisesMessage(ValidationError, 'Insufficient'):
            PR.issue(self.a, p.pk, self.gyp, 60)
        PR.issue(self.a, p.pk, self.clk, 95, 'Silo 1')
        PR.issue(self.a, p.pk, self.gyp, 8, 'Shed')  # 8 vs standard 5 → outside 5 %
        self.assertEqual(Production.objects.get(pk=p.pk).status, 'In progress')
        with self.assertRaisesMessage(ValidationError, 'outside tolerance for Gypsum'):
            PR.complete(self.a, p.pk, 100)
        p = PR.complete(self.a, p.pk, 100, 0, 'Gypsum raised for setting time trial')
        self.assertEqual((p.status, p.material_cost, p.unit_cost), ('Quality hold', D('297000.00'), D('2970.0000')))  # 95×3000 + 8×1500
        self.assertEqual(bal(self.site, self.opc), 0)  # nothing in stock until release

    def test_specs_results_release_and_alert(self):
        call_command('seed_quality_specs', verbosity=0)
        self.assertEqual(QualitySpec.objects.filter(product=self.opc).count(), 12)
        s28 = QualitySpec.objects.get(product=self.opc, parameter='Compressive strength 28 days')
        self.assertEqual((s28.min_value, s28.required_for_release), (D('53'), False))
        self.recipe()
        p = self.run_batch()
        eq = LabEquipment.objects.create(site=self.site, code='CTM-1', name='Compression tester', calibration_due=timezone.localdate() - timedelta(days=1))
        req = list(QualitySpec.objects.filter(product=self.opc, required_for_release=True))
        s3 = QualitySpec.objects.get(product=self.opc, parameter='Compressive strength 3 days')
        with self.assertRaisesMessage(ValidationError, 'calibrate it'):
            QC.record_result(self.qm, p.pk, s3, 30, eq)
        with self.assertRaisesMessage(ValidationError, 'Results still needed'):
            QC.release(self.qm, p.pk)
        good = {'Fineness (Blaine)': 310, 'Initial setting time': 120, 'Final setting time': 220, 'Soundness – Le Chatelier': 1, 'Soundness – Autoclave': '0.1',
                'MgO': '2.1', 'SO₃': '2.4', 'Loss on ignition': '2.0', 'Chloride': '0.02', 'Compressive strength 3 days': 25}  # 25 < 27 → out of spec
        for s in req:
            QC.record_result(self.qm, p.pk, s, good[s.parameter])
        with self.assertRaisesMessage(ValidationError, 'already has a result'):
            QC.record_result(self.qm, p.pk, s3, 29)
        with self.assertRaisesMessage(ValidationError, 'Out of specification'):
            QC.release(self.qm, p.pk)
        QC.record_result(self.qm, p.pk, s3, 29, retest_reason='Cube mould leak in first set')
        self.assertEqual(p.results.filter(spec=s3).count(), 2)  # first value kept on record
        with self.assertRaisesMessage(ValidationError, 'different quality user'):
            QC.release(self.a, p.pk)
        QC.release(self.qm, p.pk)
        m = StockMovement.objects.get(source=f'production:{p.pk}')
        self.assertEqual((bal(self.site, self.opc), m.unit_cost), (D(100), D('2925.0000')))
        QC.record_result(self.qm, p.pk, s28, 51)  # after release, below 53
        self.assertTrue(AuditEvent.objects.filter(action='Quality alert', reference=p.batch).exists())
        self.assertEqual(len(QC.trace(p)['issues']), 2)
        # A second batch out of spec: deviation needs a note; reject keeps it out of stock
        p2 = self.run_batch()
        for s in req:
            QC.record_result(self.qm, p2.pk, s, good[s.parameter])
        with self.assertRaisesMessage(ValidationError, 'justification'):
            QC.release(self.qm, p2.pk, 'deviation')
        QC.release(self.qm, p2.pk, 'reject', 'Low early strength')
        self.assertEqual((Production.objects.get(pk=p2.pk).status, bal(self.site, self.opc)), ('Rejected', D(100)))

    def test_legacy_flow_without_specs_still_works(self):
        p = Production.objects.create(site=self.site, product=self.clk, created_by=self.a, planned_quantity=10, actual_quantity=9, batch='LEG-1', shift='A')
        transition_production(self.a, p.pk, 'complete')
        with self.assertRaisesMessage(ValidationError, 'passing test'):
            transition_production(self.b, p.pk, 'release')

    def test_packing_reconciliation_and_shift_log(self):
        move_stock(self.a, self.site, self.opc, D(60), 'o-opc', 'Opening', unit_cost=D(3000))
        data = {'date': timezone.localdate(), 'shift': 'A', 'packer': 'Packer 1', 'bulk_product': self.opc, 'bag_product': self.bag, 'batch': None,
                'bulk_issued': D('50.6'), 'bags_packed': 1000, 'bags_burst': 4, 'spillage_kg': D(100), 'downtime_minutes': 0, 'empty_bags': self.sack}
        with self.assertRaisesMessage(ValidationError, 'record the reason'):
            PR.pack(self.a, self.site, data)  # 50.6 − 50 − 0.1 = 0.5 MT ≈ 1 %
        run = PR.pack(self.a, self.site, {**data, 'difference_reason': 'Silo meter reads high — calibration booked'})
        self.assertEqual((run.difference_mt, bal(self.site, self.bag), bal(self.site, self.opc), bal(self.site, self.sack)), (D('0.500'), D(1000), D('9.4'), D(3996)))
        self.assertEqual(run.unit_cost, D('159.8320'))  # (50.6 × 3000 + 1004 × 8) / 1000
        base = {'site': self.site, 'date': timezone.localdate(), 'shift': 'A', 'line': 'Kiln 1', 'production': None, 'run_hours': D(8), 'downtime_minutes': 30,
                'downtime_reason': '', 'power_kwh': D(12000), 'output': D(2400), 'meter_opening': None, 'meter_closing': None, 'crew': '', 'supervisor': 'R. Verma', 'remarks': ''}
        with self.assertRaisesMessage(ValidationError, 'downtime reason'):
            PR.save_shift_log(self.a, base)
        log = PR.save_shift_log(self.a, {**base, 'downtime_reason': 'mechanical'})
        self.assertEqual(log.kwh_per_unit, D('5.00'))
        with self.assertRaisesMessage(ValidationError, 'already logged'):
            PR.save_shift_log(self.a, {**base, 'downtime_reason': 'mechanical'})

    def test_batches_for_dispatch_and_screens(self):
        call_command('seed_quality_specs', verbosity=0)
        self.recipe()
        p = self.run_batch()
        self.client.force_login(self.a)
        for url in [reverse('production'), reverse('production_new'), reverse('production_detail', args=[p.pk]), reverse('trace', args=[p.pk]), reverse('recipes'),
                    reverse('recipe_new'), reverse('shift_logs'), reverse('packing'), reverse('quality')]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertRedirects(self.client.get(reverse('coa', args=[p.pk])), reverse('production_detail', args=[p.pk]))
        Production.objects.filter(pk=p.pk).update(status='Released', released_at=timezone.now(), released_by=self.qm)
        self.assertEqual(list(batches_for(self.site, self.opc)), [Production.objects.get(pk=p.pk)])
        self.assertContains(self.client.get(reverse('coa', args=[p.pk])), 'Test certificate')
        r = self.client.post(reverse('production_new'), {'site': self.site.pk, 'product': self.opc.pk, 'planned_quantity': '50', 'shift': 'C', 'line': 'CM2'})
        new = Production.objects.exclude(pk=p.pk).get()
        self.assertRedirects(r, reverse('production_detail', args=[new.pk]))
