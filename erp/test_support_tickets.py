"""Help & support tickets: raising, visibility, agent replies and internal notes, assignment, resolve / close / reopen, queue."""
from datetime import timedelta

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import support as S
from .insights import my_queue
from .models import SupportTicket


@override_settings(EMAIL_OTP_ENABLED=False)
class SupportTicketTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('qa-user', 'u@example.com', 'Verification-Password-816!')
        self.other = User.objects.create_user('qa-other')
        self.agent = User.objects.create_user('qa-agent')
        self.agent.user_permissions.add(Permission.objects.get(codename='handle_supportticket'))
        self.agent = User.objects.get(pk=self.agent.pk)

    def test_full_cycle(self):
        with self.assertRaises(ValidationError):
            S.raise_ticket(self.user, '', 'short')
        t = S.raise_ticket(self.user, 'Cannot approve order', 'Approve button gives an error on SO-12', 'problem', 'urgent', 'Sales orders', '/orders/12/')
        self.assertTrue(t.number)
        self.assertAlmostEqual((t.due_at - t.created_at).total_seconds(), 4 * 3600, delta=5)
        self.assertEqual(list(S.visible(self.other)), [])
        with self.assertRaises(PermissionDenied):
            S.reply(self.other, t.pk, 'hi')
        with self.assertRaises(PermissionDenied):
            S.reply(self.user, t.pk, 'secret', internal=True)
        self.assertIn('Support tickets not assigned', {i['label'] for i in my_queue(self.agent, [])})
        S.reply(self.agent, t.pk, 'Checked logs — credit limit issue', internal=True)
        S.reply(self.agent, t.pk, 'Your dealer is over the credit limit; finance can override.')
        t.refresh_from_db()
        self.assertEqual((t.status, t.assigned_to, bool(t.first_response_at)), ('waiting', self.agent, True))
        self.assertIn('Support answers waiting for you', {i['label'] for i in my_queue(self.user, [])})
        S.reply(self.user, t.pk, 'Still need help')
        self.assertEqual(SupportTicket.objects.get(pk=t.pk).status, 'progress')
        with self.assertRaises(PermissionDenied):
            S.resolve(self.user, t.pk, 'done')
        S.resolve(self.agent, t.pk, 'Explained override; finance approved')
        with self.assertRaisesMessage(ValidationError, 'Rate from 1 to 5'):
            S.close(self.user, t.pk, 9)
        S.close(self.user, t.pk, 5)
        t.refresh_from_db()
        self.assertEqual((t.status, t.rating), ('closed', 5))
        S.reopen(self.user, t.pk, 'Happened again')
        self.assertEqual(SupportTicket.objects.get(pk=t.pk).status, 'progress')
        S.resolve(self.agent, t.pk, 'Fixed')
        SupportTicket.objects.filter(pk=t.pk).update(resolved_at=timezone.now() - timedelta(days=8))
        with self.assertRaisesMessage(ValidationError, 'raise a new ticket'):
            S.reopen(self.user, t.pk, 'again')

    def test_screens(self):
        self.client.force_login(self.user)
        r = self.client.post(reverse('ticket_new'), {'subject': 'Need a report', 'description': 'Monthly district-wise sales in bags please', 'category': 'feature',
                                                     'priority': 'low', 'page_url': '/sales/'})
        t = SupportTicket.objects.get()
        self.assertRedirects(r, reverse('ticket_detail', args=[t.pk]))
        S.reply(self.agent, t.pk, 'internal only', internal=True)
        page = self.client.get(reverse('ticket_detail', args=[t.pk]))
        self.assertContains(page, 'Monthly district-wise')
        self.assertNotContains(page, 'internal only')
        self.assertEqual(self.client.get(reverse('tickets')).status_code, 200)
        self.assertContains(self.client.get(reverse('ticket_new') + '?from=/sales/'), 'value="/sales/"')
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('ticket_detail', args=[t.pk])).status_code, 404)
        self.client.force_login(self.agent)
        self.assertContains(self.client.get(reverse('ticket_detail', args=[t.pk])), 'internal only')
        self.client.post(reverse('ticket_action', args=[t.pk, 'assign']), {'agent': self.agent.pk})
        self.client.post(reverse('ticket_action', args=[t.pk, 'resolve']), {'resolution': 'Added to report backlog'})
        self.assertEqual(SupportTicket.objects.get(pk=t.pk).status, 'resolved')
        self.assertContains(self.client.get(reverse('tickets') + '?status=all'), t.number)
