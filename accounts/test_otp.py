import re
from datetime import timedelta
from smtplib import SMTPException
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from .models import EmailOTPChallenge, AuthRateLimit
from erp.models import AuditEvent


@override_settings(EMAIL_OTP_ENABLED=True, EMAIL_OTP_SKIP_FOR_USERNAME=False, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailOTPTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('otp-user', email='person@example.com', password='Original-Password-816!')

    def start(self, client=None, **extra):
        return (client or self.client).post(reverse('login'), {'username': 'otp-user', 'password': 'Original-Password-816!', **extra})

    def code(self):
        return re.search(r'code is: (\d{6})', mail.outbox[-1].body).group(1)

    def test_password_alone_does_not_authenticate(self):
        self.assertRedirects(self.start(), reverse('otp_verify'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertTrue(mail.outbox[0].alternatives)
        self.assertIn('cid:sistech-brand', mail.outbox[0].alternatives[0].content)
        self.assertEqual(mail.outbox[0].attachments[0]['Content-ID'], '<sistech-brand>')
        challenge = EmailOTPChallenge.objects.get()
        self.assertNotEqual(challenge.code_hash, self.code())
        self.assertNotIn(self.code(), repr(dict(self.client.session)))
        self.assertNotContains(self.client.get(reverse('otp_verify')), self.user.email)

    def test_old_password_only_session_cannot_bypass_new_second_step(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_email_change_requires_session_reverification(self):
        self.start()
        self.client.post(reverse('otp_verify'), {'code': self.code()})
        self.user.email = 'changed@example.com'
        self.user.save(update_fields=['email'])
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_success_rotates_session_restores_next_and_remember(self):
        self.start(remember_me='on', next='/accounts/profile/')
        old_key = self.client.session.session_key
        response = self.client.post(reverse('otp_verify'), {'code': self.code()})
        self.assertRedirects(response, reverse('profile'))
        self.assertNotEqual(old_key, self.client.session.session_key)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.user.pk))
        self.assertEqual(self.client.session.get_expiry_age(), 28800)
        self.assertTrue(EmailOTPChallenge.objects.get().consumed_at)
        self.assertNotIn('otp_challenge', self.client.session)
        self.assertTrue(AuditEvent.objects.filter(action='Sign-in code verified').exists())

    def test_invalid_password_sends_nothing(self):
        self.client.post(reverse('login'), {'username': 'otp-user', 'password': 'wrong'})
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(EmailOTPChallenge.objects.exists())

    def test_expired_code_rejected(self):
        self.start()
        code = self.code()
        EmailOTPChallenge.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertContains(self.client.post(reverse('otp_verify'), {'code': code}), 'expired')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_wrong_attempts_persist_and_lock_out_correct_code(self):
        self.start()
        correct = self.code()
        wrong = '000000' if correct != '000000' else '111111'
        for _ in range(5): self.client.post(reverse('otp_verify'), {'code': wrong})
        self.assertEqual(EmailOTPChallenge.objects.get().attempts, 5)
        self.assertContains(self.client.post(reverse('otp_verify'), {'code': correct}), 'attempt limit')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_code_is_bound_to_browser(self):
        self.start()
        other = Client()
        session = other.session
        session['otp_challenge'] = str(EmailOTPChallenge.objects.get().pk)
        session.save()
        self.assertRedirects(other.post(reverse('otp_verify'), {'code': self.code()}), reverse('login'))
        self.assertNotIn('_auth_user_id', other.session)

    def test_single_use_after_logout(self):
        self.start()
        challenge = EmailOTPChallenge.objects.get()
        code = self.code()
        self.client.post(reverse('otp_verify'), {'code': code})
        self.client.post(reverse('logout'))
        session = self.client.session
        session['otp_challenge'] = str(challenge.pk)
        session.save()
        self.assertRedirects(self.client.post(reverse('otp_verify'), {'code': code}), reverse('login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_resend_cooldown_and_superseded_code(self):
        self.start()
        original = self.code()
        self.client.post(reverse('otp_resend'))
        self.assertEqual(len(mail.outbox), 1)
        EmailOTPChallenge.objects.update(created_at=timezone.now()-timedelta(seconds=61))
        with patch('accounts.otp.secrets.randbelow', return_value=999999 if original != '999999' else 888888):
            self.client.post(reverse('otp_resend'))
        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(EmailOTPChallenge.objects.filter(consumed_at__isnull=True).count(), 1)
        self.assertContains(self.client.post(reverse('otp_verify'), {'code': original}), 'incorrect')
        self.assertRedirects(self.client.post(reverse('otp_verify'), {'code': self.code()}), '/')

    def test_resend_and_verify_require_csrf(self):
        self.assertEqual(self.client.get(reverse('otp_resend')).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse('otp_verify'), {'code': '123456'}).status_code, 403)
        self.assertEqual(client.post(reverse('otp_resend')).status_code, 403)

    def test_delivery_failure_never_authenticates(self):
        with patch('accounts.otp.EmailMultiAlternatives.send', side_effect=SMTPException('transport error')):
            response = self.start()
        self.assertContains(response, 'couldn’t send')
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertTrue(EmailOTPChallenge.objects.get().consumed_at)
        self.assertFalse(EmailOTPChallenge.objects.get().delivered)

    def test_disabled_email_or_password_change_invalidates_pending_challenge(self):
        for change in ['is_active', 'email', 'password']:
            with self.subTest(change=change):
                EmailOTPChallenge.objects.all().delete()
                AuthRateLimit.objects.all().delete()
                self.user.is_active = True
                self.user.email = 'person@example.com'
                self.user.set_password('Original-Password-816!')
                self.user.save()
                self.start()
                code = self.code()
                if change == 'is_active': self.user.is_active = False
                elif change == 'email': self.user.email = 'other@example.com'
                else: self.user.set_password('Changed-Password-816!')
                self.user.save()
                self.assertRedirects(self.client.post(reverse('otp_verify'), {'code': code}), reverse('login'))

    def test_preauthentication_expires_after_fifteen_minutes(self):
        self.start()
        EmailOTPChallenge.objects.update(password_verified_at=timezone.now()-timedelta(minutes=16))
        self.assertRedirects(self.client.get(reverse('otp_verify')), reverse('login'))

    def test_open_redirect_stays_blocked_after_otp(self):
        self.start(next='https://evil.example/')
        self.assertRedirects(self.client.post(reverse('otp_verify'), {'code': self.code()}), '/')

    def test_missing_or_demo_email_blocked_without_bypass(self):
        for email in ['', 'admin@example.test']:
            self.user.email = email
            self.user.save()
            self.start()
            self.assertEqual(len(mail.outbox), 0)
            self.assertNotIn('_auth_user_id', self.client.session)

    def test_repeated_password_submission_reuses_existing_challenge(self):
        self.start()
        self.start()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailOTPChallenge.objects.count(), 1)


@override_settings(EMAIL_OTP_ENABLED=True, EMAIL_OTP_SKIP_FOR_USERNAME=True, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class UsernameOrEmailSignInTests(TestCase):
    """Username + password signs in directly; email + password requires the email code."""
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('plant-user', email='plant.user@example.com', password='Original-Password-816!')

    def test_username_signs_in_directly_without_otp(self):
        response = self.client.post(reverse('login'), {'username': 'plant-user', 'password': 'Original-Password-816!'})
        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.session['_auth_user_id'], str(self.user.pk))
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)

    def test_email_requires_otp(self):
        response = self.client.post(reverse('login'), {'username': 'Plant.User@example.com', 'password': 'Original-Password-816!'})
        self.assertRedirects(response, reverse('otp_verify'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)

    def test_username_wrong_password_rejected(self):
        response = self.client.post(reverse('login'), {'username': 'plant-user', 'password': 'wrong'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_disabling_skip_requires_otp_for_username(self):
        with self.settings(EMAIL_OTP_SKIP_FOR_USERNAME=False):
            response = self.client.post(reverse('login'), {'username': 'plant-user', 'password': 'Original-Password-816!'})
            self.assertRedirects(response, reverse('otp_verify'))

    def test_username_session_revoked_when_skip_disabled(self):
        self.client.post(reverse('login'), {'username': 'plant-user', 'password': 'Original-Password-816!'})
        with self.settings(EMAIL_OTP_SKIP_FOR_USERNAME=False):
            self.assertEqual(self.client.get(reverse('profile')).status_code, 302)
            self.assertNotIn('_auth_user_id', self.client.session)


@override_settings(EMAIL_OTP_ENABLED=True, EMAIL_OTP_SKIP_FOR_USERNAME=True)
class AccountSessionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('session-user', email='session.user@example.com', password='Original-Password-816!')

    def sign_in(self, client):
        client.post(reverse('login'), {'username': 'session-user', 'password': 'Original-Password-816!'}, HTTP_USER_AGENT='Mozilla/5.0 (Windows NT 10.0) Chrome/120.0')

    def test_sign_in_records_method_ip_and_browser(self):
        self.sign_in(self.client)
        event = AuditEvent.objects.filter(actor=self.user, action='Account signed in').latest('created_at')
        self.assertIn('Username + password', event.detail)
        self.assertIn('Chrome on Windows', event.detail)

    def test_profile_shows_activity_and_other_sessions(self):
        other = Client(); self.sign_in(other); self.sign_in(self.client)
        response = self.client.get(reverse('profile'))
        self.assertContains(response, 'Recent sign-in activity')
        self.assertEqual(response.context['other_sessions'], 1)

    def test_sign_out_other_devices_keeps_current_session(self):
        other = Client(); self.sign_in(other); self.sign_in(self.client)
        self.client.post(reverse('sign_out_other_sessions'))
        self.assertEqual(self.client.get(reverse('profile')).status_code, 200)
        self.assertEqual(other.get(reverse('profile')).status_code, 302)
        self.assertTrue(AuditEvent.objects.filter(actor=self.user, action='Other sessions signed out').exists())

    def test_sign_out_other_devices_requires_post(self):
        self.sign_in(self.client)
        self.assertEqual(self.client.get(reverse('sign_out_other_sessions')).status_code, 405)
