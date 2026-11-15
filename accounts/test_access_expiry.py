from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import authenticate
from django.contrib.auth.models import Permission, User
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.forms import MemberForm
from accounts.models import UserProfile
from accounts.security import access_allowed
from erp.models import AuditEvent


@override_settings(EMAIL_OTP_ENABLED=False)
class TemporaryAccessTests(TestCase):
    def setUp(self):
        self.password = 'Expiry-Verification-816!'
        self.user = User.objects.create_user('temporary', 'temporary@sistech.co.in', self.password)
        self.profile = UserProfile.for_user(self.user)
        self.profile.access_expires_at = timezone.now() + timedelta(hours=1)
        self.profile.save()
        self.user.user_permissions.add(Permission.objects.get(codename='view_product', content_type__app_label='erp'))

    def expire(self):
        self.profile.access_expires_at = timezone.now() - timedelta(seconds=1)
        self.profile.save()

    def test_expiry_blocks_password_login_and_cached_permissions_at_exact_boundary(self):
        self.assertIsNotNone(authenticate(username=self.user.username, password=self.password))
        self.assertTrue(self.user.has_perm('erp.view_product'))
        deadline = self.profile.access_expires_at
        with patch('accounts.security.timezone.now', return_value=deadline):
            self.assertFalse(access_allowed(self.user))
            self.assertIsNone(authenticate(username=self.user.username, password=self.password))
            self.assertFalse(self.user.has_perm('erp.view_product'))

    def test_all_existing_sessions_lose_access_before_cleanup(self):
        other = Client()
        self.client.force_login(self.user)
        other.force_login(self.user)
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)
        self.expire()
        for client in (self.client, other):
            response = client.get(reverse('profile'))
            self.assertRedirects(response, reverse('login') + '?next=' + reverse('profile'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)  # denial does not depend on the scheduler

    def test_cleanup_is_idempotent_and_preserves_unexpired_users(self):
        self.client.force_login(self.user)
        other = User.objects.create_user('ongoing')
        client = Client()
        client.force_login(other)
        other_session = client.session.session_key
        self.expire()
        for _ in range(2):
            call_command('expire_temporary_access', stdout=StringIO())
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(Session.objects.filter(session_key=self.client.session.session_key).exists())
        self.assertTrue(Session.objects.filter(session_key=other_session).exists())
        self.assertEqual(AuditEvent.objects.filter(action='Temporary access expired').count(), 1)

    def test_no_expiry_and_admin_renewal(self):
        self.expire()
        self.assertFalse(access_allowed(self.user))
        self.profile.access_expires_at = None
        self.profile.save()
        self.assertTrue(access_allowed(self.user))
        self.assertIsNotNone(authenticate(username=self.user.username, password=self.password))

    def test_form_and_ordinary_user_cannot_edit_policy(self):
        data = dict(first_name='Temp', last_name='', email=self.user.email, is_active='on',
                    access_expires_at=(timezone.localtime() - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'))
        form = MemberForm(data, instance=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('access_expires_at', form.errors)
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(reverse('member_edit', args=[self.user.pk]), data).status_code, 403)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_expiry_invalidates_pending_otp_and_blocks_new_code(self):
        from django.test import RequestFactory
        from django.core.exceptions import ValidationError
        from accounts.otp import issue_challenge, pending_challenge, verify_challenge
        request = RequestFactory().get('/')
        request.session = self.client.session
        request.session.save()
        challenge = issue_challenge(request, self.user)
        request.session['otp_challenge'] = str(challenge.pk)
        self.assertIsNotNone(pending_challenge(request))
        self.expire()
        self.assertIsNone(pending_challenge(request))
        self.assertIsNone(verify_challenge(request, '123456')[0])
        with self.assertRaises(ValidationError):
            issue_challenge(request, self.user)

    def test_admin_policy_renewal_revokes_old_sessions(self):
        self.client.force_login(self.user)
        old_key = self.client.session.session_key
        admin = User.objects.create_superuser('expiry-admin', 'expiry-admin@sistech.co.in', self.password)
        client = Client()
        client.force_login(admin)
        response = client.post(reverse('member_edit', args=[self.user.pk]), {
            'first_name': 'Temporary', 'last_name': '', 'email': self.user.email, 'is_active': 'on',
            'access_expires_at': (timezone.localtime() + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
        })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Session.objects.filter(session_key=old_key).exists())
        self.profile.refresh_from_db()
        self.assertGreater(self.profile.access_expires_at, timezone.now())
        self.assertTrue(AuditEvent.objects.filter(action='User access updated', reference=f'user:{self.user.pk}').exists())
