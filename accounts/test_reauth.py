from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from accounts.reauth import SESSION_KEY, confirm, recent, safe_destination, sensitive
from erp.models import AuditEvent


@override_settings(EMAIL_OTP_ENABLED=False)
class SensitiveAuthenticationTests(TestCase):
    def setUp(self):
        self.password = 'Sensitive-Verification-816!'
        self.user = User.objects.create_superuser('sensitive-admin', 'sensitive-admin@sistech.co.in', self.password)
        self.client.force_login(self.user)

    def test_wrong_password_and_throttle_cannot_create_confirmation(self):
        for _ in range(5):
            response = self.client.post(reverse('reauthenticate'), {'password': 'wrong', 'next': '/accounts/access/'})
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(SESSION_KEY, self.client.session)
        response = self.client.post(reverse('reauthenticate'), {'password': self.password})
        self.assertContains(response, 'Too many confirmation attempts')
        self.assertNotIn(SESSION_KEY, self.client.session)
        self.assertEqual(AuditEvent.objects.filter(action='Sensitive confirmation failed').count(), 5)
        self.assertFalse(AuditEvent.objects.filter(detail__contains=self.password).exists())

    def test_success_rotates_session_returns_to_review_and_expires(self):
        before = self.client.session.session_key
        response = self.client.post(reverse('reauthenticate'), {'password': self.password, 'next': '/accounts/access/'})
        self.assertRedirects(response, '/accounts/access/')
        self.assertNotEqual(self.client.session.session_key, before)
        request = RequestFactory().get('/')
        request.session, request.user = self.client.session, self.user
        self.assertTrue(recent(request))
        with patch('accounts.reauth.timezone.now', return_value=timezone.now() + timedelta(seconds=300)):
            self.assertFalse(recent(request))
        self.assertEqual(AuditEvent.objects.filter(action='Sensitive identity confirmed').count(), 1)

    def test_confirmation_is_bound_to_user_credentials_email_and_session(self):
        request = RequestFactory().get('/')
        request.session, request.user = self.client.session, self.user
        confirm(request.session, self.user)
        self.assertTrue(recent(request))
        request.session.cycle_key()
        self.assertFalse(recent(request))
        confirm(request.session, self.user)
        self.user.email = 'changed@sistech.co.in'
        self.assertFalse(recent(request))
        confirm(request.session, self.user)
        self.user.set_password('Changed-Verification-816!')
        self.assertFalse(recent(request))
        confirm(request.session, self.user)
        request.user = User.objects.create_user('different')
        self.assertFalse(recent(request))

    def test_sensitive_post_is_not_executed_or_replayed(self):
        name = 'unconfirmed-role'
        count = AuditEvent.objects.count()
        response = self.client.post(reverse('role_new'), {'name': name})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('reauthenticate')))
        from django.contrib.auth.models import Group
        self.assertFalse(Group.objects.filter(name=name).exists())
        self.assertEqual(AuditEvent.objects.count(), count)
        self.client.post(reverse('reauthenticate'), {'password': self.password, 'next': reverse('role_new')})
        self.assertFalse(Group.objects.filter(name=name).exists())  # no automatic replay
        response = self.client.post(reverse('role_new'), {'name': name})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Group.objects.filter(name=name).exists())

    def test_return_urls_and_malformed_or_future_proofs_fail_closed(self):
        for url in ['https://evil.example', '//evil.example', '/\\evil.example', 'http://[', '/\nlocation', 'javascript:alert(1)', '/%2fevil.example', '/%5cevil.example', '/%0alocation']:
            self.assertEqual(safe_destination(url), '/')
        response = self.client.post(reverse('reauthenticate'), {'password': self.password, 'next': '//evil.example'})
        self.assertRedirects(response, '/')
        request = RequestFactory().get('/')
        request.session, request.user = self.client.session, self.user
        for value in [None, 'bad', {'at': True}, {'at': float('nan')}, {'at': float('inf')}, {'at': 10 ** 1000}]:
            request.session[SESSION_KEY] = value
            self.assertFalse(recent(request))
        confirm(request.session, self.user)
        request.session[SESSION_KEY]['at'] = (timezone.now() + timedelta(minutes=1)).timestamp()
        self.assertFalse(recent(request))

    def test_confirmation_does_not_grant_permissions(self):
        ordinary = User.objects.create_user('ordinary', password=self.password)
        client = Client()
        client.force_login(ordinary)
        client.post(reverse('reauthenticate'), {'password': self.password})
        self.assertEqual(client.post(reverse('role_new'), {'name': 'Forbidden'}).status_code, 403)

    def test_route_inventory_covers_required_sensitive_action_categories(self):
        routes = [
            ('payment_action', [1, 'release']), ('journal_action', [1, 'approve']),
            ('period_action', [1, 'reopen']), ('year_action', [1, 'approve_reopen']),
            ('scheme_payout_new', [1]), ('scheme_payout_decide', [1, 'approve']),
            ('order_action', [1, 'approve']), ('quotation_action', [1, 'approve']),
            ('damage_decide', [1]), ('count_action', [1, 'approve']),
            ('transfer_action', [1, 'resolve']),
            ('partners_edit', [1]), ('organization_edit', []),
            ('crm_application_edit', [1]), ('crm_application_action', [1, 'credit']),
            ('member_edit', [1]), ('role_edit', [1]),
        ]
        factory = RequestFactory()
        for name, args in routes:
            url = reverse(name, args=args)
            self.assertTrue(sensitive(factory.post(url), resolve(url)), name)
            self.assertFalse(sensitive(factory.get(url), resolve(url)), name)
            response = self.client.post(url)
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response.url.startswith(reverse('reauthenticate')), name)
        url = reverse('payments')
        self.assertTrue(sensitive(factory.get(url, {'export': 'bank'}), resolve(url)))
        response = self.client.get(url, {'export': 'bank'})
        self.assertTrue(response.url.startswith(reverse('reauthenticate')))

    def test_complete_password_login_establishes_recent_confirmation(self):
        self.client.logout()
        self.client.post(reverse('login'), {'username': self.user.username, 'password': self.password})
        self.assertIn(SESSION_KEY, self.client.session)

    def test_csrf_and_authentication_are_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('reauthenticate')).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('reauthenticate'), {'password': self.password}).status_code, 403)
        self.assertEqual(client.post(reverse('role_new'), {'name': 'No CSRF'}).status_code, 403)

    def test_actual_journal_stays_draft_until_confirmed_resubmission(self):
        from erp import gl
        from erp.models import JournalLine
        maker = User.objects.create_superuser('voucher-maker', 'maker@sistech.co.in', self.password)
        gl.ensure_chart()
        voucher = gl.create_voucher(maker, timezone.localdate(), None, 'Verification capital deposit', [
            (gl.acct('bank_main'), 100, 0, None, ''), (gl.acct('capital'), 0, 100, None, ''),
        ])
        count = JournalLine.objects.filter(entry__status='posted').count()
        url = reverse('journal_action', args=[voucher.pk, 'approve'])
        response = self.client.post(url)
        self.assertIn('next=%2Faccounts-ledger%2Fjournal%2F', response.url)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'draft')
        self.assertEqual(JournalLine.objects.filter(entry__status='posted').count(), count)
        self.client.post(reverse('reauthenticate'), {'password': self.password})
        self.client.post(url)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, 'posted')
        self.assertEqual(JournalLine.objects.filter(entry=voucher, entry__status='posted').count(), 2)

    def test_actual_payment_cannot_release_until_confirmed(self):
        from decimal import Decimal
        from erp import gl, treasury as TR
        from erp.models import JournalEntry, Partner, Site
        maker = User.objects.create_superuser('payment-maker', 'pay-maker@sistech.co.in', self.password)
        site = Site.objects.create(code='STEP', name='Step-up verification plant', kind='Plant')
        supplier = Partner.objects.create(code='STEP-S', name='Verification supplier', kind='Supplier',
                                          bank_account_number='1234567890', bank_ifsc='SBIN0000001')
        gl.ensure_chart()
        asset = TR.capitalise(maker, dict(code='STEP-FA', name='Verification machine', category='fa_plant',
            site=site, supplier=supplier, put_to_use=timezone.localdate(), cost=Decimal('1000'),
            salvage=Decimal('0'), method='slm', life_years=Decimal('10')), 'ap')
        payment = TR.propose(maker, supplier, gl.acct('bank_main'), {('asset', asset.pk): asset.cost})
        TR.decide(self.user, payment.pk, True)
        count = JournalEntry.objects.count()
        url = reverse('payment_action', args=[payment.pk, 'release'])
        response = self.client.post(url, {'reference': 'QA-STEPUP-UTR'})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('reauthenticate')))
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'approved')
        self.assertEqual(JournalEntry.objects.count(), count)
        self.client.post(reverse('reauthenticate'), {'password': self.password})
        self.client.post(url, {'reference': 'QA-STEPUP-UTR'})
        payment.refresh_from_db()
        self.assertEqual(payment.status, 'paid')
        self.assertEqual(JournalEntry.objects.count(), count + 1)
