from datetime import datetime, timedelta
from unittest.mock import patch
from django.contrib.auth import authenticate
from django.contrib.auth.models import User, Group, Permission
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse, resolve, Resolver404
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from erp.models import Site, SiteAccess, Product, Partner, Order, OrderLine, AuditEvent
from .models import AuthRateLimit
from .roles import initialize_roles, MANAGED_CODES


@override_settings(EMAIL_OTP_ENABLED=False)
class AuthenticationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('teammate', email='person@example.test', password='Original-Password-816!')

    def sign_in(self, **extra):
        return self.client.post(reverse('login'), {'username': 'person@example.test', 'password': 'Original-Password-816!', **extra})

    def test_email_and_username_case_insensitive(self):
        for identifier in ['person@example.test', 'PERSON@EXAMPLE.TEST', 'teammate', 'TEAMMATE']:
            self.assertEqual(authenticate(username=identifier, password='Original-Password-816!'), self.user)

    def test_ambiguous_email_and_inactive_accounts_rejected(self):
        User.objects.create_user('duplicate', email=self.user.email, password='Original-Password-816!')
        self.assertIsNone(authenticate(username=self.user.email, password='Original-Password-816!'))
        self.user.is_active = False
        self.user.save()
        self.assertIsNone(authenticate(username=self.user.username, password='Original-Password-816!'))

    def test_signin_session_and_auth_audit(self):
        response = self.sign_in()
        self.assertRedirects(response, '/')
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action='Account signed in').exists())

    def test_remember_me_is_eight_hours(self):
        self.sign_in(remember_me='on')
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), 28800)

    def test_open_redirect_rejected_and_internal_next_honoured(self):
        response = self.sign_in(next='https://evil.example/phish')
        self.assertEqual(response['Location'], '/')
        self.client.logout()
        response = self.sign_in(next='/accounts/profile/')
        self.assertEqual(response['Location'], '/accounts/profile/')

    def test_rate_limit_and_window_expiry(self):
        for _ in range(5):
            self.client.post(reverse('login'), {'username': self.user.email, 'password': 'wrong'})
        response = self.sign_in()
        self.assertContains(response, 'Too many sign-in attempts')
        self.assertNotIn('_auth_user_id', self.client.session)
        AuthRateLimit.objects.update(started_at=timezone.now() - timedelta(minutes=16))
        self.assertRedirects(self.sign_in(), '/')

    def test_login_validation_does_not_reflect_password(self):
        response = self.client.post(reverse('login'), {'username': 'unknown@example.test', 'password': 'DoNotReflect-983!'})
        self.assertContains(response, 'Check your details')
        self.assertNotContains(response, 'DoNotReflect-983!')

    def test_login_and_logout_csrf_and_post_only(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse('login'), {'username': 'teammate', 'password': 'test'}).status_code, 403)
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertRedirects(self.client.post(reverse('logout')), reverse('signed_out'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_login_uses_same_throttled_form(self):
        for _ in range(5):
            self.client.post('/admin/login/', {'username': self.user.username, 'password': 'wrong'})
        response = self.client.post(reverse('login'), {'username': self.user.username, 'password': 'Original-Password-816!'})
        self.assertContains(response, 'Too many sign-in attempts')

    def test_nonstaff_admin_redirect_does_not_loop(self):
        self.client.force_login(self.user)
        response = self.client.get('/admin/', follow=True)
        self.assertEqual(response.status_code, 403)
        self.assertLess(len(response.redirect_chain), 3)

    def test_current_password_attempts_are_limited(self):
        self.client.force_login(self.user)
        for _ in range(5):
            self.client.post(reverse('password_change'), {'old_password': 'wrong', 'new_password1': 'Changed-Password-619!', 'new_password2': 'Changed-Password-619!'})
        response = self.client.post(reverse('password_change'), {'old_password': 'Original-Password-816!', 'new_password1': 'Changed-Password-619!', 'new_password2': 'Changed-Password-619!'})
        self.assertContains(response, 'Too many attempts')

    def test_public_auth_pages_render_with_shared_design(self):
        for name in ['login', 'password_reset', 'password_reset_done', 'password_reset_complete', 'signed_out', 'access_help']:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'accounts/auth.css')
            self.assertContains(response, 'erp/fonts.css')
        self.assertContains(self.client.get(reverse('password_reset_confirm', args=['invalid', 'invalid'])), 'expired')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_recovery_generic_for_existing_unknown_and_inactive(self):
        existing = self.client.post(reverse('password_reset'), {'email': self.user.email})
        self.assertEqual(len(mail.outbox), 1)
        unknown = self.client.post(reverse('password_reset'), {'email': 'unknown@example.test'})
        self.assertEqual(existing['Location'], unknown['Location'])
        self.assertEqual(len(mail.outbox), 1)
        self.user.is_active = False
        self.user.save()
        self.client.post(reverse('password_reset'), {'email': self.user.email})
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_ambiguous_recovery_sends_no_email(self):
        User.objects.create_user('duplicate', email=self.user.email, password='Test-Password-747!')
        self.client.post(reverse('password_reset'), {'email': self.user.email})
        self.assertEqual(len(mail.outbox), 0)

    def reset_url(self):
        return reverse('password_reset_confirm', args=[urlsafe_base64_encode(force_bytes(self.user.pk)), default_token_generator.make_token(self.user)])

    def test_password_reset_link_single_use(self):
        original_url = self.reset_url()
        response = self.client.get(original_url)
        url = response['Location']
        self.assertContains(self.client.get(url), 'Set a new password')
        response = self.client.post(url, {'new_password1': 'Changed-Password-619!', 'new_password2': 'Changed-Password-619!'})
        self.assertRedirects(response, reverse('password_reset_complete'))
        self.assertIsNone(authenticate(username='teammate', password='Original-Password-816!'))
        self.assertIsNotNone(authenticate(username='teammate', password='Changed-Password-619!'))
        self.assertContains(self.client.get(original_url), 'expired')

    def test_password_reset_expired(self):
        url = self.reset_url()
        with patch('django.contrib.auth.tokens.PasswordResetTokenGenerator._now', return_value=datetime.now() + timedelta(minutes=31)):
            self.assertContains(self.client.get(url), 'expired')

    def test_password_change_validates_and_invalidates_other_sessions(self):
        other = Client()
        self.client.force_login(self.user)
        other.force_login(self.user)
        url = reverse('password_change')
        weak = self.client.post(url, {'old_password': 'Original-Password-816!', 'new_password1': '123', 'new_password2': '123'})
        self.assertEqual(weak.status_code, 200)
        response = self.client.post(url, {'old_password': 'Original-Password-816!', 'new_password1': 'Changed-Password-619!', 'new_password2': 'Changed-Password-619!'})
        self.assertRedirects(response, reverse('account_security'))
        self.assertIsNotNone(self.user.profile.password_changed_at if hasattr(self.user, 'profile') else __import__('accounts.models', fromlist=['UserProfile']).UserProfile.objects.get(user=self.user).password_changed_at)
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)
        self.assertEqual(other.get(reverse('profile')).status_code, 302)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_explicit_otp_feature_flag_disables_challenge(self):
        self.sign_in()
        self.assertEqual(len(mail.outbox), 0)
        self.client.logout()
        self.assertRedirects(self.client.get('/accounts/otp/verify/'), reverse('login'))
        self.assertRedirects(self.client.post('/accounts/otp/resend/'), reverse('login'))


@override_settings(EMAIL_OTP_ENABLED=False)
class RoleAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        initialize_roles()
        cls.admin = User.objects.create_superuser('admin', 'admin@example.test', 'Admin-Password-916!')
        cls.sales = User.objects.create_user('sales', email='sales@example.test', password='Sales-Password-916!')
        cls.sales.groups.add(Group.objects.get(name='Sales executive'))
        cls.site = Site.objects.create(code='P1', name='Main Plant', kind='Plant')
        cls.other_site = Site.objects.create(code='P2', name='Other Plant', kind='Plant')
        SiteAccess.objects.create(user=cls.sales, site=cls.site)
        cls.product = Product.objects.create(code='C1', name='Cement', category='Cement', unit='MT')
        cls.supplier = Partner.objects.create(code='S1', name='Hidden Supplier', kind='Supplier')
        cls.order = Order.objects.create(site=cls.site, partner=cls.supplier, kind='Purchase', created_by=cls.admin)
        OrderLine.objects.create(order=cls.order, product=cls.product, quantity=100, rate=500)

    def test_sales_role_cannot_access_purchase_routes_or_dashboard_data(self):
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse('sales')).status_code, 200)
        self.assertEqual(self.client.get(reverse('purchases')).status_code, 403)
        self.assertEqual(self.client.get(reverse('order_new', args=['Purchase'])).status_code, 403)
        self.assertEqual(self.client.get(reverse('order_detail', args=[self.order.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse('order_action', args=[self.order.pk, 'submit'])).status_code, 403)
        dashboard = self.client.get('/')
        self.assertNotContains(dashboard, self.order.number)
        self.assertNotContains(dashboard, 'Hidden Supplier')
        self.assertNotContains(dashboard, 'href="/purchases/"')
        self.assertNotContains(dashboard, 'href="/accounts/access/"')

    def test_access_administration_requires_superuser(self):
        self.client.force_login(self.sales)
        for name in ['access_control', 'role_new', 'member_new']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
            self.assertEqual(self.client.post(reverse(name), {}).status_code, 403)

    def test_custom_matrix_persists_and_rejects_privilege_injection(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('role_new'), {'name': 'Dispatch only', 'permissions': ['view_sales_order', 'post_sales_order']})
        self.assertRedirects(response, reverse('access_control'))
        group = Group.objects.get(name='Dispatch only')
        self.assertEqual(set(group.permissions.values_list('codename', flat=True)), {'view_sales_order', 'post_sales_order'})
        response = self.client.post(reverse('role_edit', args=[group.pk]), {'name': 'Escalated', 'permissions': ['change_user']})
        self.assertContains(response, 'outside the ERP role matrix')
        group.refresh_from_db()
        self.assertEqual(group.name, 'Dispatch only')
        self.assertFalse(group.permissions.filter(codename='change_user').exists())

    def test_matrix_requires_view_before_action_and_unique_role_name(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('role_new'), {'name': 'Incomplete', 'permissions': ['post_sales_order']})
        self.assertContains(response, 'Enable View')
        self.assertFalse(Group.objects.filter(name='Incomplete').exists())
        response = self.client.post(reverse('role_new'), {'name': 'sales EXECUTIVE'})
        self.assertContains(response, 'already exists')

    def test_role_change_immediately_revokes_existing_session(self):
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(reverse('sales')).status_code, 200)
        group = Group.objects.get(name='Sales executive')
        group.permissions.clear()
        self.assertEqual(self.client.get(reverse('sales')).status_code, 403)

    def test_export_requires_separate_permission(self):
        viewer = User.objects.create_user('report-viewer')
        viewer.user_permissions.add(Permission.objects.get(codename='view_stockmovement'))
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('reports')).status_code, 200)
        self.assertEqual(self.client.get('/reports/?export=csv').status_code, 403)

    def test_new_member_assigns_roles_sites_and_cannot_escalate(self):
        self.client.force_login(self.admin)
        role = Group.objects.get(name='Sales executive')
        response = self.client.post(reverse('member_new'), {'first_name': 'New', 'last_name': 'Member', 'username': 'new-member', 'email': 'new@example.test', 'password1': 'Initial-Password-665!', 'password2': 'Initial-Password-665!', 'is_active': 'on', 'roles': [role.pk], 'sites': [self.site.pk], 'is_superuser': 'on', 'is_staff': 'on'})
        self.assertRedirects(response, reverse('access_control'))
        user = User.objects.get(username='new-member')
        self.assertTrue(user.check_password('Initial-Password-665!'))
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.is_staff)
        self.assertEqual(list(user.groups.all()), [role])
        self.assertTrue(SiteAccess.objects.filter(user=user, site=self.site).exists())

    def test_duplicate_email_rejected_case_insensitively(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('member_edit', args=[self.sales.pk]), {'email': 'ADMIN@EXAMPLE.TEST', 'is_active': 'on'})
        self.assertContains(response, 'already associated')
        self.sales.refresh_from_db()
        self.assertEqual(self.sales.email, 'sales@example.test')

    def test_superuser_cannot_be_disabled_through_member_editor(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(reverse('member_edit', args=[self.admin.pk]), {'email': self.admin.email}).status_code, 403)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_disabling_user_revokes_existing_session(self):
        client = Client()
        client.force_login(self.sales)
        self.client.force_login(self.admin)
        self.client.post(reverse('member_edit', args=[self.sales.pk]), {'email': self.sales.email})
        self.assertEqual(client.get(reverse('sales')).status_code, 302)

    def test_staff_cannot_escalate_through_django_admin(self):
        self.sales.is_staff = True
        self.sales.save()
        self.sales.user_permissions.add(Permission.objects.get(codename='change_user'), Permission.objects.get(codename='change_group'))
        self.client.force_login(self.sales)
        self.assertEqual(self.client.get(f'/admin/auth/user/{self.sales.pk}/change/').status_code, 403)
        self.assertEqual(self.client.get('/admin/auth/group/').status_code, 403)

    def test_admin_pages_and_profile_render(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse('password_change_done')), reverse('account_security'))
        for name in ['access_control', 'role_new', 'member_new', 'profile', 'profile_edit', 'account_security', 'account_activity', 'account_contacts', 'contact_new', 'password_change']:
            with self.subTest(name=name): self.assertEqual(self.client.get(reverse(name)).status_code, 200)
        group = Group.objects.get(name='Sales executive')
        self.assertEqual(self.client.get(reverse('role_edit', args=[group.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('member_edit', args=[self.sales.pk])).status_code, 200)

    def test_setup_roles_preserves_edits_unless_reset(self):
        role = Group.objects.get(name='Sales executive')
        role.permissions.clear()
        initialize_roles()
        self.assertFalse(role.permissions.exists())
        initialize_roles(update=True)
        self.assertTrue(role.permissions.filter(codename='view_sales_order').exists())
