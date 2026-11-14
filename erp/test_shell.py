from django.contrib.auth.models import User, Group, Permission
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(EMAIL_OTP_ENABLED=False)
class AppShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser('shell-admin', 'shell.admin@example.com', 'Original-Password-816!')
        cls.viewer = User.objects.create_user('shell-viewer', 'shell.viewer@example.com', 'Original-Password-816!')
        group = Group.objects.create(name='Shell sales viewer')
        group.permissions.add(Permission.objects.get(codename='view_sales_order'))
        cls.viewer.groups.add(group)

    def test_superuser_sees_grouped_navigation_and_admin_items(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('dashboard'))
        labels = [g['label'] for g in response.context['nav_groups']]
        self.assertEqual(labels[:3], ['Overview', 'Sales', 'Procurement'])
        self.assertContains(response, 'href="/accounts/access/"')
        self.assertContains(response, 'Design &amp; developed by')

    def test_viewer_only_gets_permitted_links_and_no_create_actions(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('dashboard'))
        self.assertContains(response, 'href="/sales/"')
        for hidden in ['/purchases/', '/inventory/', '/accounts/access/', '/admin/']:
            self.assertNotContains(response, f'href="{hidden}"')
        self.assertEqual(response.context['quick_create'], [])

    def test_roadmap_modules_are_listed_without_links(self):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse('dashboard'))
        soon = [i for g in response.context['nav_groups'] for i in g['items'] if i['soon']]
        self.assertTrue(soon)
        self.assertTrue(all(i['url'] is None for i in soon))
        self.assertContains(response, 'Accounts &amp; ledgers')

    def test_breadcrumb_marks_active_section_for_sub_pages(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('order_new', args=['Purchase']))
        self.assertEqual(response.context['nav_current']['label'], 'Purchase orders')
        self.assertContains(response, 'aria-current="page"')
