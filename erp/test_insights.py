"""Dashboards (Step 9): net sales and tonnage, scoping, receivables, work queue, drill-down, permissions and reminder emails."""
from datetime import date, timedelta
from decimal import Decimal as D

from django.contrib.auth.models import User, Permission
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import insights as I
from .models import InvoiceLine, Order, OrderLine, SiteAccess, Partner
from .templatetags.erp_format import inr_short, mt
from .test_schemes import invoice_fixture, credit_fixture, receipt_fixture
from .test_support import seed_business_data


def perms(*codes):
    return Permission.objects.filter(content_type__app_label='erp', codename__in=codes)


@override_settings(EMAIL_OTP_ENABLED=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', ERP_BASE_URL='https://erp.example.test')
class InsightTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        Partner.objects.filter(pk=self.customer.pk).update(district='Indore')
        self.inv = invoice_fixture(self, [(self.bag, D(1000))])
        InvoiceLine.objects.filter(invoice=self.inv).update(quantity=200)           # 200 bags × 50 kg = 10 MT
        self.inv2 = invoice_fixture(self, [(self.bulk, D(500))])                     # 1 MT bulk
        self.far = invoice_fixture(self, [(self.bulk, D(9999))], site=self.other)    # other plant: never in maker's numbers
        credit_fixture(self, self.inv, 100, self.inv.lines.get())                   # returns 0.1 bag
        self.period = I.parse_period({'p': 'mtd'})

    def test_period_parsing(self):
        p = I.parse_period({'p': 'ytd'}, date(2026, 2, 10))
        self.assertEqual((p.start, p.end, p.prev_start, p.label), (date(2025, 4, 1), date(2026, 2, 10), date(2024, 4, 1), 'FY 2025-26'))
        p = I.parse_period({'p': 'last_month'}, date(2026, 3, 15))
        self.assertEqual((p.start, p.end, p.prev_start, p.prev_end), (date(2026, 2, 1), date(2026, 2, 28), date(2026, 1, 1), date(2026, 1, 28)))
        p = I.parse_period({'p': 'qtd'}, date(2026, 11, 14))
        self.assertEqual((p.start, p.label), (date(2026, 10, 1), 'Q3 FY 2026-27'))
        bad = I.parse_period({'p': 'custom', 'from': '2026-05-01', 'to': '2026-04-01'}, date(2026, 11, 14))
        self.assertEqual(bad.key, 'mtd')
        ok = I.parse_period({'p': 'custom', 'from': '2026-05-01', 'to': '2026-05-10'})
        self.assertEqual((ok.days, ok.prev_start, ok.prev_end), (10, date(2026, 4, 21), date(2026, 4, 30)))

    def test_net_sales_tonnage_and_site_scope(self):
        s = I.sales_summary([self.site], self.period)
        self.assertEqual(s['value'], D('1400'))                     # 1000 + 500 − 100 credit note
        self.assertEqual(s['mt'], D('10.995'))                      # 10 + 1 − 0.1 bag × 0.05
        self.assertEqual(s['invoices'], 2)
        self.assertEqual({r['label']: r['mt'] for r in s['products']}, {'Finished bags': D('9.995'), 'Finished bulk': D('1.000')})
        self.assertEqual(s['districts'][0]['label'], 'Indore')
        self.assertEqual(s['owners'][0]['value'], D('1400'))
        self.assertEqual(I.sales_summary([self.other], self.period)['value'], D('9999'))
        t = I.trend([self.site], 3)
        self.assertEqual((len(t), t[-1]['mt'], t[-1]['h']), (3, D('10.995'), D('100.0')))

    def test_receivables_ageing_and_credit_limit(self):
        receipt_fixture(self, self.inv2, D('590'))                                   # inv2 fully paid
        self.inv.due_date = self.today - timedelta(days=45)
        self.inv.save()
        Partner.objects.filter(pk=self.customer.pk).update(credit_limit=500)
        r = I.receivables(self.period)
        overdue_inv = D('1180') - D('118')                                            # invoice − credit note
        self.assertEqual(r['ageing'][2]['amount'], overdue_inv)                       # 31–60 days
        self.assertEqual(r['overdue'], overdue_inv)
        self.assertEqual(r['overdue_dealers'][0]['days'], 45)
        self.assertEqual(r['collected'], D('590'))
        self.assertTrue(any(x['partner'].pk == self.customer.pk for x in r['over_limit']))

    def test_my_queue_follows_maker_checker(self):
        o = Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker, status='Submitted')
        OrderLine.objects.create(order=o, product=self.bag, quantity=10, rate=300)
        labels = lambda u: {i['label']: i['count'] for i in I.my_queue(u)}
        self.assertNotIn('Sales orders to approve', labels(self.maker))
        self.assertEqual(labels(self.checker)['Sales orders to approve'], 1)
        self.assertNotIn('Sales orders to approve', labels(self.outsider))            # other plant
        o.status = 'Approved'; o.save()
        book = I.order_book([self.site])
        self.assertEqual((book['orders'], book['mt']), (1, D('0.500')))

    def test_screens_permissions_and_drill(self):
        self.client.force_login(self.checker)
        for url in [reverse('dashboard'), reverse('dashboard') + '?p=ytd', reverse('my_work')] + \
                   [reverse('insight_area', args=[a]) + '?p=ytd' for a in ['executive', 'sales', 'credit', 'stock', 'dispatch', 'supply', 'plant', 'quality', 'finance']] + \
                   [reverse('insight_drill', args=[k]) for k in ['sales', 'collections', 'overdue', 'orders', 'dispatches', 'supply', 'production', 'complaints']]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        r = self.client.get(reverse('insight_drill', args=['sales']) + f'?product={self.bulk.pk}')
        self.assertContains(r, self.inv2.number)
        self.assertNotContains(r, self.inv.number)
        self.assertNotContains(r, self.far.number)                                    # other plant
        self.assertEqual(self.client.get(reverse('insight_area', args=['nope'])).status_code, 404)
        self.assertEqual(self.client.get(reverse('my_work') + '?format=json').json()['count'], sum(i['count'] for i in I.my_queue(self.checker)))
        # A sales person without management / finance rights sees only their own home and no company money
        rep = User.objects.create_user('qa-rep', 'rep@example.com', 'Verification-Password-816!')
        rep.user_permissions.set(perms('view_sales_order', 'create_sales_order', 'view_crmlead', 'add_crmlead'))
        SiteAccess.objects.create(user=rep, site=self.site)
        self.client.force_login(rep)
        home = self.client.get(reverse('dashboard'))
        self.assertContains(home, 'My sales')
        self.assertNotContains(home, 'Overdue receivable')
        for a in ['executive', 'sales', 'credit', 'finance']:
            self.assertEqual(self.client.get(reverse('insight_area', args=[a])).status_code, 403, a)
        self.assertEqual(self.client.get(reverse('insight_drill', args=['collections'])).status_code, 403)
        # Finance dashboards need the explicit company-finance scope, not just the module permission
        fin = User.objects.create_user('qa-fin')
        fin.user_permissions.set(perms('view_receipt', 'view_financials'))
        self.client.force_login(fin)
        self.assertEqual(self.client.get(reverse('insight_area', args=['credit'])).status_code, 403)
        fin.user_permissions.add(*perms('access_company_finance'))
        fin = User.objects.get(pk=fin.pk)
        self.client.force_login(fin)
        self.assertEqual(self.client.get(reverse('insight_area', args=['credit'])).status_code, 200)

    def test_reminder_email_digest(self):
        Order.objects.create(site=self.site, partner=self.customer, kind='Sale', created_by=self.maker, status='Submitted')
        call_command('send_reminders', '--dry-run', stdout=open('/dev/null', 'w'))
        self.assertEqual(len(mail.outbox), 0)
        call_command('send_reminders', stdout=open('/dev/null', 'w'))
        to = {m.to[0]: m for m in mail.outbox}
        self.assertIn('checker@example.com', to)
        self.assertNotIn('maker@example.com', [k for k, m in to.items() if 'Sales orders to approve' in m.body])
        self.assertIn('https://erp.example.test/sales/?status=Submitted', to['checker@example.com'].body)

    def test_formats(self):
        self.assertEqual([inr_short(950), inr_short(12400), inr_short(325000), inr_short(14200000), inr_short(-5000000)], ['₹950', '₹12.4 K', '₹3.25 L', '₹1.42 Cr', '−₹50 L'])
        self.assertEqual(mt(D('12345.678')), '12,345.7 MT')


@override_settings(EMAIL_OTP_ENABLED=False)
class InsightAreaTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.inv = invoice_fixture(self, [(self.bag, D(1000))])
        self.client.force_login(self.checker)

    def test_new_dashboards_definitions_and_export(self):
        for a in ['dealers', 'schemes', 'procurement', 'risk']:
            self.assertEqual(self.client.get(reverse('insight_area', args=[a]) + '?p=ytd').status_code, 200, a)
        d = I.dealer_network([self.site], I.parse_period({'p': 'mtd'}))
        self.assertEqual((d['active'], d['new']), (1, 1))
        self.assertContains(self.client.get(reverse('insight_definitions')), 'Realisation')
        r = self.client.get(reverse('insight_drill', args=['sales']) + '?p=mtd&export=csv')
        body = r.content.decode('utf-8-sig')
        self.assertEqual(r['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn(self.inv.number, body)
        self.assertIn('Generated', body.splitlines()[0])
        from .models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(action='Dashboard export', reference='sales').exists())

    def test_risk_lists_overrides(self):
        from .services import transition_order
        Partner.objects.filter(pk=self.customer.pk).update(credit_limit=100)
        o = Order.objects.create(kind='Sale', site=self.site, partner=self.customer, created_by=self.maker)
        OrderLine.objects.create(order=o, product=self.bulk, quantity=1, rate=500, gst_rate=18)
        transition_order(self.maker, o.pk, 'submit')
        transition_order(self.checker, o.pk, 'approve', override_reason='Advance cheque', override_until=self.today + timedelta(days=3), backorder_reason='Partner truck')
        r = I.risk_summary([self.site], I.parse_period({'p': 'mtd'}))
        self.assertEqual((r['credit_overrides'], r['backorders']), (1, 1))
        self.assertContains(self.client.get(reverse('insight_area', args=['risk'])), o.number)
