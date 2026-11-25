"""Sales team charts: per-person net sales, targets, collections, and who may see whose numbers."""
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from . import team as T
from . import insights as I

from .models import SalesTarget, SiteAccess
from .test_projects import seed_projects
from .test_schemes import invoice_fixture, credit_fixture, receipt_fixture


def perms(*codes):
    return Permission.objects.filter(codename__in=codes)


@override_settings(EMAIL_OTP_ENABLED=False)
class SalesTeamTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.inv = invoice_fixture(self, [(self.bag, D('1000'))])
        from .models import InvoiceLine
        credit_fixture(self, self.inv, 100, line=InvoiceLine.objects.filter(invoice=self.inv).first())
        receipt_fixture(self, self.inv, D('500'))
        SalesTarget.objects.create(site=self.site, owner=self.maker, month=self.today.replace(day=1), sales_amount=D('1800'), updated_by=self.maker)
        self.manager = User.objects.create_user('qa-sales-manager')
        self.manager.user_permissions.set(perms('view_invoice', 'view_team_crm'))
        SiteAccess.objects.create(user=self.manager, site=self.site)

    def test_numbers_are_net_and_attributed_to_order_creator(self):
        period = I.parse_period({})
        data = T.team([self.site], period)
        row = next(r for r in data['rows'] if r['user'] == self.maker)
        self.assertEqual(row['value'], D('900'))           # 1000 invoiced − 100 credit note
        self.assertEqual(row['target'], D('1800'))
        self.assertEqual(row['pct'], D('50.0'))
        self.assertGreater(row['collected'], 0)
        self.assertEqual(data['chart']['series'][0]['label'], row['name'])
        p = T.person(self.maker, [self.site], period)
        self.assertEqual(p['value'], D('900'))
        self.assertEqual(len(p['bars']), 12)
        self.assertEqual(p['bars'][-1]['target'], D('1800'))

    def test_manager_sees_team_and_charts(self):
        self.client.force_login(self.manager)
        r = self.client.get(reverse('sales_team'))
        self.assertContains(r, 'Who sold how much')
        self.assertContains(r, '<polyline', html=False)
        self.assertContains(r, reverse('sales_person', args=[self.maker.pk]))
        p = self.client.get(reverse('sales_person', args=[self.maker.pk]))
        self.assertContains(p, 'Last 12 months')
        self.assertContains(p, self.inv.number)

    def test_sales_executive_sees_only_own_page(self):
        rep = User.objects.create_user('qa-exec')
        rep.user_permissions.set(perms('create_sales_order', 'view_sales_order'))
        SiteAccess.objects.create(user=rep, site=self.site)
        self.client.force_login(rep)
        self.assertEqual(self.client.get(reverse('sales_team')).status_code, 403)
        self.assertEqual(self.client.get(reverse('sales_person', args=[self.maker.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('sales_person', args=[rep.pk])).status_code, 200)
