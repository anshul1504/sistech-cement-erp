"""Star programmes: verified usage, volumes, compliance, ranking levels and publishing."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import recognition as RC
from .models import Influencer, Partner, RecognitionProgram, RecognitionLevel, RecognitionAward, CreditNote, CreditNoteLine
from .test_schemes import invoice_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class RecognitionTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.boss = User.objects.create_superuser('rc-boss', 'b@example.com', 'Verification-Password-816!')

    def program(self, audience, **levels):
        p = RecognitionProgram.objects.create(name=f'Star {audience}', audience=audience, starts_on=self.today - timedelta(days=60), ends_on=self.today, created_by=self.maker)
        for level, (n, minimum) in levels.items():
            RecognitionLevel.objects.create(program=p, level=level, winners=n, minimum=minimum, reward=f'{level} award')
        return p

    def dealer(self, code, district, state, bags):
        d = Partner.objects.create(code=code, name=f'Dealer {code}', kind='Dealer', district=district, state=state, credit_limit=10 ** 8, payment_terms_days=30)
        inv = invoice_fixture(self, [(self.bag, D(1000))], partner=d)
        inv.lines.update(quantity=bags)
        return d

    def test_dealer_ranking_levels_and_compliance(self):
        big = self.dealer('A', 'Indore', '23', 2000)    # 100 MT
        mid = self.dealer('B', 'Indore', '23', 1200)    # 60 MT
        dhar = self.dealer('C', 'Dhar', '23', 800)      # 40 MT
        raipur = self.dealer('D', 'Raipur', '22', 1000)  # 50 MT
        CreditNoteLine.objects.create(credit_note=CreditNote.objects.create(number='RC-CN', invoice=mid.invoices.get(), partner=mid, reason='return', taxable=0, cgst=0, sgst=0,
                                                                           igst=0, round_off=0, total=0, created_by=self.maker), invoice_line=mid.invoices.get().lines.get(), quantity=200, taxable=0, gst_rate=18, cgst=0, sgst=0, igst=0)
        vols = {w.code: v for w, _, _, v in RC.volumes(self.program('dealer'))}
        self.assertEqual(vols, {'A': D('100.000'), 'B': D('50.000'), 'C': D('40.000'), 'D': D('50.000')})
        p = self.program('dealer', national=(1, 0), state=(1, 0), district=(1, 45))
        with self.assertRaisesMessage(ValidationError, 'Run the ranking'):
            RC.publish(self.boss, p.pk, True)
        RC.rank(self.maker, p.pk)
        got = {(a.level, a.area): a.partner.code for a in RecognitionAward.objects.filter(program=p)}
        # A is national only; B wins Madhya Pradesh; D wins Chhattisgarh; Dhar has no one above 45 MT
        self.assertEqual(got, {('national', 'India'): 'A', ('state', 'Madhya Pradesh'): 'B', ('state', 'Chhattisgarh'): 'D'})
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            RC.publish(self.maker, p.pk, True)
        RC.publish(self.boss, p.pk, True)
        a = RecognitionAward.objects.get(program=p, level='national')
        with self.assertRaisesMessage(ValidationError, 'Record how'):
            RC.fulfil(self.boss, a.pk, '')
        RC.fulfil(self.boss, a.pk, 'Trophy given at annual meet')
        # Compliance: a dealer with a long-overdue invoice is not eligible
        old = invoice_fixture(self, [(self.bag, D(500))], partner=big, on=self.today - timedelta(days=50))
        old.__class__.objects.filter(pk=old.pk).update(due_date=self.today - timedelta(days=40))
        p2 = self.program('dealer', national=(1, 0))
        p2.exclude_overdue_days = 30; p2.save()
        RC.rank(self.maker, p2.pk)
        self.assertNotEqual(RecognitionAward.objects.get(program=p2).partner, big)

    def test_influencer_usage_counts_only_when_verified(self):
        mason = Influencer.objects.create(kind='Mason', name='Ramesh', mobile='9876500001', district='Indore', state='23', primary_dealer=self.customer)
        engineer = Influencer.objects.create(kind='Engineer', name='Er. Jain', mobile='9876500002', district='Indore', state='23')
        u = RC.record_usage(self.maker, mason, self.customer, self.bag, 120, self.today, 'b-77')
        with self.assertRaisesMessage(ValidationError, 'already recorded'):
            RC.record_usage(self.maker, mason, self.customer, self.bag, 10, self.today, 'B-77')
        RC.record_usage(self.maker, engineer, self.customer, self.bag, 300, self.today, 'B-78')
        p = self.program('mason', district=(2, 0))
        self.assertEqual(RC.volumes(p), [])  # nothing verified yet
        with self.assertRaisesMessage(ValidationError, 'cannot verify'):
            RC.verify_usage(self.maker, u.pk, True)
        RC.verify_usage(self.checker, u.pk, True)
        self.assertEqual([(w, v) for w, _, _, v in RC.volumes(p)], [(mason, D(120))])  # engineer is in the other programme
        RC.rank(self.maker, p.pk)
        self.assertEqual(RecognitionAward.objects.get(program=p).influencer, mason)
        self.client.force_login(self.boss)
        for url in [reverse('recognition'), reverse('recognition_detail', args=[p.pk]), reverse('influencer_usage')]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('recognition'), {'audience': 'engineer', 'name': 'Star Engineer 2026-27', 'starts_on': '2026-04-01', 'ends_on': '2027-03-31',
                                                      'overdue': '0', 'district_on': 'on', 'district_winners': '3', 'district_min': '100', 'district_reward': 'Domestic couple trip'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(RecognitionProgram.objects.get(name='Star Engineer 2026-27').levels.get().winners, 3)
