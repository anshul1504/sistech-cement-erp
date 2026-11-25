"""Cost centres and budgets: voucher cost-centre lines and reversals, versioned maker-checker budgets, CSV import, budget vs actual."""
from datetime import date
from decimal import Decimal as D

from django.core.exceptions import ValidationError, PermissionDenied
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import budgets as B, gl
from .models import Account, CostCentre, Budget, JournalLine
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class BudgetTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.fy = B.fy_of(self.today)
        self.power, self.sales = gl.acct('power'), gl.acct('sales_cement')

    def test_cost_centre_on_vouchers(self):
        cc = B.save_cost_centre(self.maker, 'plt', 'Plant A', 'plant', self.site)
        self.assertEqual(cc.code, 'PLT')
        with self.assertRaisesMessage(ValidationError, 'already exists'):
            B.save_cost_centre(self.maker, 'PLT', 'Dup')
        je = gl.create_voucher(self.maker, self.today, self.site, 'Power provision', [(self.power, D(500), 0, None, 'Oct', cc), (gl.acct('bank_main'), 0, D(500), None, '')])
        gl.decide_voucher(self.checker, je.pk, True)
        self.assertEqual(JournalLine.objects.get(entry=je, account=self.power).cost_centre, cc)
        with self.assertRaisesMessage(ValidationError, 'cannot change'):
            B.save_cost_centre(self.maker, 'PLT2', 'Plant A', pk=cc.pk)
        B.save_cost_centre(self.maker, 'PLT', 'Plant A', active=False, pk=cc.pk)
        with self.assertRaisesMessage(ValidationError, 'inactive'):
            gl.create_voucher(self.maker, self.today, None, 'x', [(self.power, D(1), 0, None, '', CostCentre.objects.get()), (gl.acct('bank_main'), 0, D(1), None, '')])

    def test_budget_versions_and_comparison(self):
        b = B.create_budget(self.maker, self.fy)
        months = B.fy_months(self.fy)
        this = months.index(self.today.replace(day=1))
        with self.assertRaisesMessage(ValidationError, 'income and expense'):
            B.set_cells(self.maker, b.pk, None, {(gl.acct('bank_main').pk, months[0]): D(1)})
        cells = {(self.power.pk, m): D(1000) for m in months}
        cells.update({(self.sales.pk, m): D(10000) for m in months})
        B.set_cells(self.maker, b.pk, None, cells)
        with self.assertRaisesMessage(ValidationError, 'unapproved version already exists'):
            B.create_budget(self.maker, self.fy)
        B.submit(self.maker, b.pk)
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            B.decide(self.maker, b.pk, True)
        B.decide(self.checker, b.pk, True)
        je = gl.create_voucher(self.maker, self.today, self.site, 'Power', [(self.power, D(700), 0, None, ''), (gl.acct('bank_main'), 0, D(700), None, '')])
        gl.decide_voucher(self.checker, je.pk, True)
        r = B.comparison(Budget.objects.get(pk=b.pk), self.today)
        power = next(x for g in r['groups'] for x in g['rows'] if x['account'] == self.power)
        self.assertEqual((power['budget'], power['actual'], power['variance'], power['year']), (D(1000) * (this + 1), D(700), D(1000) * (this + 1) - D(700), D(12000)))
        v2 = B.create_budget(self.maker, self.fy, 'Revised', copy_from=b)
        self.assertEqual(v2.lines.count(), 24)
        n = B.import_csv(self.maker, v2.pk, 'Account code,Cost centre,Apr\n' + f'{self.power.code},,2500\n')
        self.assertEqual(n, 12)
        self.assertEqual(v2.lines.get(account=self.power, month=months[0]).amount, D(2500))
        with self.assertRaisesMessage(ValidationError, 'not an active'):
            B.import_csv(self.maker, v2.pk, '9999,,1\n')
        B.submit(self.maker, v2.pk); B.decide(self.checker, v2.pk, True)
        self.assertEqual(Budget.objects.get(pk=b.pk).status, 'superseded')

    def test_screens_and_finance_scope(self):
        plain = User.objects.create_user('qa-no-fin')
        with self.assertRaises(PermissionDenied):
            B.create_budget(plain, self.fy)
        self.client.force_login(self.checker)
        r = self.client.post(reverse('budgets'), {'fy_start': self.fy.isoformat(), 'name': 'FY plan'})
        b = Budget.objects.get()
        self.assertRedirects(r, reverse('budget_detail', args=[b.pk]))
        self.client.post(reverse('budget_detail', args=[b.pk]), {f'c_{self.power.pk}_0': '1,500'})
        self.assertEqual(b.lines.get().amount, D(1500))
        for url in [reverse('budgets'), reverse('budget_detail', args=[b.pk]), reverse('budget_detail', args=[b.pk]) + '?export=csv', reverse('budget_vs_actual'),
                    reverse('budget_vs_actual') + f'?budget={b.pk}&upto=2000-01-01', reverse('cost_centres'), reverse('voucher_new')]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.client.post(reverse('cost_centres'), {'code': 'ADM', 'name': 'Administration', 'kind': 'department', 'active': '1'})
        self.assertTrue(CostCentre.objects.filter(code='ADM').exists())
