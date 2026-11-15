from accounts.test_support import force_login_recent
from django.contrib.auth.models import User, Group
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse

from erp.models import Site, SiteAccess, AuditEvent
from .models import UserProfile
from .roles import initialize_roles

PASSWORD = 'Original-Password-816!'


@override_settings(EMAIL_OTP_ENABLED=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class AdministrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        initialize_roles()
        cls.admin = User.objects.create_superuser('adm', 'adm@sistech.co.in', PASSWORD)
        cls.member = User.objects.create_user('ravi', 'ravi@sistech.co.in', PASSWORD, first_name='Ravi')
        cls.member.groups.add(Group.objects.get(name='Sales executive'))
        cls.site = Site.objects.create(code='PLT-9', name='Satna Plant', kind='Plant')
        SiteAccess.objects.create(user=cls.member, site=cls.site)

    def setUp(self):
        force_login_recent(self.client, self.admin)

    def test_pages_render_and_are_superuser_only(self):
        role = Group.objects.get(name='Sales executive')
        urls = [reverse('access_control'), reverse('member_detail', args=[self.member.pk]), reverse('member_edit', args=[self.member.pk]),
                reverse('member_set_password', args=[self.member.pk]), reverse('roles'), reverse('role_detail', args=[role.pk]),
                reverse('role_edit', args=[role.pk]), reverse('role_duplicate', args=[role.pk]), reverse('system_settings')]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        other = Client(); force_login_recent(other, self.member)
        for url in urls:
            with self.subTest(member=url):
                self.assertEqual(other.get(url).status_code, 403)

    def test_users_list_filters(self):
        page = self.client.get(reverse('access_control') + f'?site={self.site.pk}')
        self.assertEqual([u.username for u in page.context['page'].object_list], ['ravi'])
        page = self.client.get(reverse('access_control') + '?q=nobody')
        self.assertEqual(page.context['page'].paginator.count, 0)
        self.assertEqual(self.client.get(reverse('access_control') + '?status=admin').context['page'].paginator.count, 1)

    def test_edit_saves_profile_fields(self):
        self.client.post(reverse('member_edit', args=[self.member.pk]), {'first_name': 'Ravi', 'last_name': 'Kumar', 'email': 'ravi@sistech.co.in',
                                                                         'is_active': 'on', 'designation': 'Dispatch supervisor', 'phone': '9876543210',
                                                                         'roles': [], 'sites': [self.site.pk]})
        self.assertEqual(UserProfile.objects.get(user=self.member).designation, 'Dispatch supervisor')

    def test_disable_enable_signs_out_and_cannot_target_self_or_superuser(self):
        device = Client(); force_login_recent(device, self.member)
        self.client.post(reverse('member_status', args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertFalse(self.member.is_active)
        self.assertEqual(device.get(reverse('profile')).status_code, 302)
        self.client.post(reverse('member_status', args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertTrue(self.member.is_active)
        self.assertEqual(self.client.post(reverse('member_status', args=[self.admin.pk])).status_code, 403)
        self.assertTrue(AuditEvent.objects.filter(action='User disabled').exists())

    def test_sign_out_everywhere(self):
        device = Client(); force_login_recent(device, self.member)
        self.client.post(reverse('member_sign_out', args=[self.member.pk]))
        self.assertEqual(device.get(reverse('profile')).status_code, 302)

    def test_reset_link_and_set_password(self):
        self.client.post(reverse('member_reset_link', args=[self.member.pk]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['ravi@sistech.co.in'])
        device = Client(); force_login_recent(device, self.member)
        response = self.client.post(reverse('member_set_password', args=[self.member.pk]), {'new_password1': 'Brand-New-Pass-771!', 'new_password2': 'Brand-New-Pass-771!'})
        self.assertRedirects(response, reverse('member_detail', args=[self.member.pk]))
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password('Brand-New-Pass-771!'))
        self.assertEqual(device.get(reverse('profile')).status_code, 302)
        self.assertEqual(UserProfile.objects.get(user=self.member).password_age_days, 0)

    def test_role_duplicate_and_delete_rules(self):
        source = Group.objects.get(name='Sales executive')
        page = self.client.get(reverse('role_duplicate', args=[source.pk]))
        self.assertEqual(page.context['name'], 'Sales executive (copy)')
        codes = list(source.permissions.values_list('codename', flat=True))
        self.client.post(reverse('role_duplicate', args=[source.pk]), {'name': 'Sales trainee', 'permissions': codes})
        copy = Group.objects.get(name='Sales trainee')
        self.assertEqual(set(copy.permissions.values_list('codename', flat=True)), set(codes))
        self.client.post(reverse('role_delete', args=[source.pk]))
        self.assertTrue(Group.objects.filter(pk=source.pk).exists())  # has a member, so kept
        self.assertRedirects(self.client.post(reverse('role_delete', args=[copy.pk])), reverse('roles'))
        self.assertFalse(Group.objects.filter(pk=copy.pk).exists())

    def test_system_settings_and_test_email(self):
        page = self.client.get(reverse('system_settings'))
        self.assertGreater(page.context['warnings'], 0)  # development defaults are flagged
        self.client.post(reverse('system_test_email'))
        self.assertEqual(mail.outbox[-1].to, ['adm@sistech.co.in'])
        self.assertTrue(AuditEvent.objects.filter(action='Test email sent').exists())

    def test_messages_render_as_toasts(self):
        response = self.client.post(reverse('member_sign_out', args=[self.member.pk]), follow=True)
        self.assertContains(response, 'toast-card is-success')
