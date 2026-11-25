"""Module launcher: home shows only permitted modules, a module scopes the sidebar to its own pages, cross-links switch module."""
from django.contrib.auth.models import User, Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from .context import MODULES, ROUTE_MODULE


@override_settings(EMAIL_OTP_ENABLED=False)
class ModuleNavigationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('mod-admin', 'm@example.test', 'x')

    def test_every_menu_route_belongs_to_exactly_one_module(self):
        seen = {}
        for m in MODULES:
            for _, items in m['sections']:
                for i in items:
                    if i.get('area'):
                        continue
                    self.assertNotIn(i['route'], seen, f"{i['route']} in {seen.get(i['route'])} and {m['key']}")
                    seen[i['route']] = m['key']
        self.assertEqual(ROUTE_MODULE['partner_detail'], 'people')

    def test_home_lists_modules_and_module_scopes_sidebar(self):
        self.client.force_login(self.admin)
        home = self.client.get(reverse('dashboard'))
        self.assertTemplateUsed(home, 'erp/launcher.html')
        self.assertContains(home, 'Sales &amp; CRM')
        self.assertContains(home, 'Finance &amp; accounts')
        r = self.client.get(reverse('module_home', args=['sales']))
        self.assertRedirects(r, reverse('insight_area', args=['sales']))
        page = self.client.get(reverse('sales'))
        self.assertEqual(page.context['nav_module']['key'], 'sales')
        sidebar = [i['label'] for g in page.context['nav_groups'] for i in g['items']]
        self.assertIn('Quotations', sidebar)
        self.assertNotIn('Tax invoices', sidebar)
        # Opening a finance page moves the sidebar to Finance
        fin = self.client.get(reverse('invoices'))
        self.assertEqual(fin.context['nav_module']['key'], 'finance')
        # Dashboard tabs inside a module show only that module's dashboards
        dash = self.client.get(reverse('insight_area', args=['sales']))
        self.assertEqual({a['key'] for a in dash.context['areas']}, {'sales', 'dealers'})
        self.assertEqual(self.client.get(reverse('module_home', args=['nope'])).status_code, 404)

    def test_single_module_user_goes_straight_in_and_hidden_modules_404(self):
        u = User.objects.create_user('one-module')
        u.user_permissions.add(Permission.objects.get(codename='view_quotation'))
        self.client.force_login(u)
        r = self.client.get(reverse('dashboard'))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get(reverse('module_home', args=['finance'])).status_code, 404)
