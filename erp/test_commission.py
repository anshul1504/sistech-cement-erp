"""Sales commission: plan approval, earn-once runs with recovery after credit notes, GL posting, TDS, access and screens."""
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import commission as C, gl
from .models import CommissionRun, JournalEntry, Order, Settlement, Receipt
from .test_schemes import invoice_fixture, credit_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class CommissionTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.rep = User.objects.create_user('qa-rep', 'rep@example.com', 'Verification-Password-816!')
        self.boss = User.objects.create_superuser('qa-boss', 'boss@example.com', 'Verification-Password-816!')
        self.start = self.today - timedelta(days=10)

    def sale(self, amount, on=None):
        inv = invoice_fixture(self, [(self.bag, D(amount))], on=on or self.today - timedelta(days=5))
        Order.objects.filter(pk=inv.order_id).update(created_by=self.rep)
        return inv

    def plan(self, **kw):
        data = {'name': 'Field 2%', 'basis': 'sales', 'rate': D(2), 'tds_rate': D(0), 'site': None, 'valid_from': self.start, 'valid_to': None, **kw}
        p = C.save_plan(self.maker, data, [self.rep])
        C.submit_plan(self.maker, p.pk)
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            C.decide_plan(self.maker, p.pk, True)
        C.decide_plan(self.checker, p.pk, True)
        return p

    def test_earn_once_recovery_and_posting(self):
        inv = self.sale(10000)
        p = self.plan(tds_rate=D(5))
        run = C.calculate(self.maker, p.pk, self.today - timedelta(days=1))
        line = run.lines.get()
        self.assertEqual((line.basis_amount, line.amount, line.tds), (D(10000), D(200), D(10)))
        with self.assertRaisesMessage(ValidationError, 'already waiting'):
            C.calculate(self.maker, p.pk, self.today)
        from django.contrib.auth.models import Permission
        self.rep.user_permissions.add(*Permission.objects.filter(codename__in=['approve_commissionrun', 'access_company_finance']))
        with self.assertRaisesMessage(ValidationError, 'own commission'):
            C.decide_run(User.objects.get(pk=self.rep.pk), run.pk, True)
        C.decide_run(self.checker, run.pk, True)
        je = JournalEntry.objects.get(source=f'com:{run.pk}')
        lines = {l.account.system_key: (l.debit, l.credit) for l in je.lines.select_related('account')}
        self.assertEqual(lines, {'commission': (D(200), 0), 'commission_payable': (0, D(190)), 'tds_payable': (0, D(10))})
        credit_fixture(self, inv, 5000)                                         # return after payout
        run2 = C.calculate(self.maker, p.pk, self.today)
        self.assertEqual((run2.start, run2.lines.get().amount), (self.today, D(-100)))
        C.decide_run(self.checker, run2.pk, True)
        rev = {l.account.system_key: (l.debit, l.credit) for l in JournalEntry.objects.get(source=f'com:{run2.pk}').lines.select_related('account')}
        self.assertEqual(rev, {'commission_payable': (D(100), 0), 'commission': (0, D(100))})
        with self.assertRaisesMessage(ValidationError, 'Nothing to calculate'):
            C.calculate(self.maker, p.pk, self.today)

    def test_bases_access_and_screens(self):
        inv = self.sale(20000)
        Settlement.objects.create(invoice=inv, receipt=Receipt.objects.create(number='QA-R9', date=self.today, partner=self.customer, amount=D(5000), created_by=self.maker), amount=D(5000))
        coll = self.plan(name='Collected', basis='collected', rate=D(1))
        self.assertEqual(C.basis(coll, self.rep, self.start, self.today), D(5000))
        mt = self.plan(name='Per MT', basis='mt', rate=D(50))
        self.assertEqual(C.basis(mt, self.rep, self.start, self.today), D('0.050'))   # 1 bag × 50 kg
        with self.assertRaises(PermissionDenied):
            C.calculate(self.rep, coll.pk, self.today)
        with self.assertRaisesMessage(ValidationError, 'at most 20'):
            C.save_plan(self.maker, {'name': 'x', 'basis': 'sales', 'rate': D(50), 'valid_from': self.today}, [self.rep])
        run = C.calculate(self.maker, coll.pk, self.today)
        self.client.force_login(self.checker)
        for url in [reverse('commission_plans'), reverse('commission_plan_new'), reverse('commission_plan', args=[coll.pk]), reverse('commission_run', args=[run.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.post(reverse('commission_run_action', args=[run.pk, 'approve']))
        self.assertEqual(CommissionRun.objects.get(pk=run.pk).status, 'draft')      # sensitive: needs recent identity confirmation
        C.decide_run(self.checker, run.pk, True)
        self.client.force_login(self.rep)
        r = self.client.get(reverse('my_commission'))
        self.assertContains(r, 'Collected')
        self.assertEqual(self.client.get(reverse('commission_run', args=[run.pk])).status_code, 403)
