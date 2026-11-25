from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from erp.models import DealerLedgerAccess, Partner, AuditEvent
from .test_support import force_login_recent


@override_settings(EMAIL_OTP_ENABLED=False)
class DealerAccessTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('access-admin', 'admin@example.test', 'Test-Password-845!')
        self.member = User.objects.create_user('dealer-member', email='dealer@example.test')
        self.dealer = Partner.objects.create(code='ACCESS-D', name='Dealer', kind='Dealer')
        self.url = reverse('dealer_access', args=[self.member.pk])
        force_login_recent(self.client, self.admin)

    def post(self, **kwargs):
        return self.client.post(self.url, {'partner': self.dealer.pk, 'active': 'on', 'reason': 'Verified dealer account ownership', **kwargs})

    def test_grant_reassign_disable_audit_and_session_revocation(self):
        from django.test import Client
        from django.contrib.sessions.models import Session
        dealer_client = Client(); dealer_client.force_login(self.member)
        key = dealer_client.session.session_key
        self.assertRedirects(self.post(), reverse('member_detail', args=[self.member.pk]))
        self.assertFalse(Session.objects.filter(session_key=key).exists())
        link = DealerLedgerAccess.objects.get(user=self.member)
        self.assertEqual(link.partner, self.dealer)
        other = Partner.objects.create(code='ACCESS-D2', name='Other dealer', kind='Dealer')
        self.post(partner=other.pk)
        self.assertEqual(DealerLedgerAccess.objects.get(user=self.member).partner, other)
        self.post(active='', partner='')
        self.assertFalse(DealerLedgerAccess.objects.get(user=self.member).active)
        self.assertEqual(AuditEvent.objects.filter(action='Dealer self-service access changed').count(), 3)

    def test_non_admin_cannot_grant_and_confirmation_is_required(self):
        force_login_recent(self.client, self.member)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        self.client.force_login(self.admin)
        response = self.post()
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('reauthenticate'), response.url)
        self.assertFalse(DealerLedgerAccess.objects.exists())

    def test_invalid_dealer_missing_email_staff_and_reason_are_rejected(self):
        supplier = Partner.objects.create(code='ACCESS-S', name='Supplier', kind='Supplier')
        for data in [{'partner': supplier.pk}, {'reason': ' '}, {'partner': ''}]:
            self.assertEqual(self.post(**data).status_code, 200)
            self.assertFalse(DealerLedgerAccess.objects.exists())
        for field, value in [('email', ''), ('is_staff', True), ('is_active', False)]:
            self.member.email, self.member.is_staff, self.member.is_active = 'dealer@example.test', False, True
            setattr(self.member, field, value); self.member.save()
            self.assertEqual(self.post().status_code, 200)
            self.assertFalse(DealerLedgerAccess.objects.exists())
