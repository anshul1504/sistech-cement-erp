"""Dealer security deposits: separate ledger, GL posting, refund limits, overdue guard, maker-checker and subledger check."""
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import deposits as DP, gl, ledger
from .models import JournalEntry
from .test_schemes import invoice_fixture
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class DepositTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.bank = gl.acct('bank_main')

    def test_receive_refund_and_controls(self):
        e = DP.receive(self.maker, self.customer, D(50000), self.bank, 'UTR1', self.today, 'Dealership deposit')
        lines = {l.account.system_key: (l.debit, l.credit) for l in JournalEntry.objects.get(source=f'dep:{e.pk}').lines.select_related('account')}
        self.assertEqual(lines, {'bank_main': (D(50000), 0), 'security_deposits': (0, D(50000))})
        self.assertEqual(ledger.balance(self.customer), 0)                    # never mixed into the trade ledger
        with self.assertRaisesMessage(ValidationError, 'available to refund'):
            DP.propose_refund(self.maker, self.customer, D(60000), self.bank, 'CHQ', self.today, 'Closed')
        inv = invoice_fixture(self, [(self.bag, D(1000))])
        inv.due_date = self.today - timedelta(days=5); inv.save()
        with self.assertRaisesMessage(ValidationError, 'overdue'):
            DP.propose_refund(self.maker, self.customer, D(10000), self.bank, 'CHQ', self.today, 'Closed')
        r = DP.propose_refund(self.maker, self.customer, D(10000), self.bank, 'CHQ', self.today, 'Limit reduced; overdue settled by receipt next week')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            DP.decide_refund(self.maker, r.pk, True)
        DP.decide_refund(self.checker, r.pk, True)
        self.assertEqual(DP.held(self.customer), D(40000))
        check = {c['name']: c for c in gl.subledger_check()}['Dealer security deposits']
        self.assertEqual((check['gl'], check['diff']), (D(40000), 0))

    def test_screen(self):
        self.client.force_login(self.checker)
        r = self.client.post(reverse('deposits'), {'partner': self.customer.pk, 'kind': 'receipt', 'amount': '25000', 'bank': self.bank.pk, 'reference': 'UTR9', 'date': self.today.isoformat()})
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(reverse('deposits') + f'?partner={self.customer.pk}'), 'UTR9')
