"""Scheme entitlements, accrual, recovery and payout (Step 8b stage 2)."""
from accounts.test_support import force_login_recent
from decimal import Decimal as D
from uuid import uuid4

from django.contrib.auth.models import User, Permission
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import rewards as RW, schemes, ledger
from .models import CreditNote, CreditNoteLine, JournalLine, SchemeEntitlementBatch, SiteAccess, Account
from .test_schemes import approved_scheme, invoice_fixture
from .test_support import seed_business_data


def bal(key, partner=None):
    qs = JournalLine.objects.filter(account__system_key=key, entry__status='posted')
    if partner: qs = qs.filter(partner=partner)
    return sum((l.debit - l.credit for l in qs), D('0'))


@override_settings(EMAIL_OTP_ENABLED=False)
class RewardTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.today = timezone.localdate()
        self.boss = User.objects.create_user('qa-finance', 'fin@example.com', 'Verification-Password-816!')
        self.boss.user_permissions.set(Permission.objects.filter(content_type__app_label='erp'))
        SiteAccess.objects.create(user=self.boss, site=self.site)

    def calc(self, version, user=None):
        return schemes.calculate(user or self.maker, version.pk, version.starts_on, version.ends_on, uuid4())

    def test_earn_once_recover_after_return_and_pay(self):
        v = approved_scheme(self)
        inv = invoice_fixture(self, [(self.bag, D(1000))])
        partial = schemes.calculate(self.maker, v.pk, v.starts_on, v.starts_on, uuid4())
        with self.assertRaisesMessage(ValidationError, 'whole scheme period'):
            RW.create_batch(self.maker, partial.pk)
        e1 = self.calc(v)
        b1 = RW.create_batch(self.maker, e1.pk)
        self.assertEqual((b1.total, b1.entitlements.get().amount), (D('20.00'), D('20.00')))  # ₹1,000 reaches the 2 % slab
        with self.assertRaisesMessage(ValidationError, 'already became'):
            RW.create_batch(self.maker, e1.pk)
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            RW.decide_batch(self.maker, b1.pk, True)
        RW.decide_batch(self.boss, b1.pk, True)
        self.assertEqual((bal('scheme_payable', self.customer), bal('sales_schemes')), (D('-20.00'), D('20.00')))
        # A return after approval: the next calculation pays only the difference (a recovery), never a second earning
        cn = CreditNote.objects.create(number='QA-CN-1', invoice=inv, partner=self.customer, reason='return', taxable=100, cgst=9, sgst=9, igst=0, round_off=0, total=118,
                                       created_by=self.maker)
        CreditNoteLine.objects.create(credit_note=cn, invoice_line=inv.lines.get(), quantity=0, taxable=100, gst_rate=18, cgst=9, sgst=9, igst=0)
        e2 = self.calc(v)
        CreditNote.objects.create(number='QA-CN-2', invoice=inv, partner=self.customer, reason='rate', taxable=10, cgst=0, sgst=0, igst=0, round_off=0, total=10, created_by=self.maker)
        with self.assertRaisesMessage(ValidationError, 'changed after this calculation'):
            RW.create_batch(self.maker, e2.pk)
        CreditNote.objects.filter(number='QA-CN-2').delete()
        b2 = RW.create_batch(self.maker, e2.pk)
        self.assertEqual([(e.calculated, e.prior, e.amount) for e in b2.entitlements.all()], [(D('9.00'), D('20.00'), D('-11.00'))])  # ₹900 → 1 %
        RW.decide_batch(self.boss, b2.pk, True)
        self.assertEqual(bal('scheme_payable', self.customer), D('-9.00'))
        # Payout as a credit to the dealer's account
        with self.assertRaisesMessage(ValidationError, 'no bank account'):
            RW.propose_payout(self.maker, self.customer, 'bank', Account.objects.get(system_key='bank_main'))
        before = ledger.balance(self.customer)
        s = RW.propose_payout(self.maker, self.customer, 'ledger')
        self.assertEqual(s.amount, D('9.00'))
        with self.assertRaisesMessage(ValidationError, 'nothing to pay'):
            RW.propose_payout(self.maker, self.customer, 'ledger')
        with self.assertRaisesMessage(ValidationError, 'maker-checker'):
            RW.decide_payout(self.maker, s.pk, True)
        RW.decide_payout(self.boss, s.pk, True)
        self.assertEqual((ledger.balance(self.customer), bal('scheme_payable', self.customer)), (before - D('9.00'), D('0')))
        self.assertTrue(inv.settlements.filter(scheme_credit=s).exists())  # settles the invoice like a credit note
        st = RW.statement(self.customer)[0]
        self.assertEqual((st['earned'], st['recovered'], st['paid'], st['balance']), (D('20.00'), D('11.00'), D('9.00'), D('0')))

    def test_cancel_payout_frees_entitlements_and_screens(self):
        v = approved_scheme(self)
        invoice_fixture(self, [(self.bag, D(2000))])
        b = RW.create_batch(self.maker, self.calc(v).pk)
        RW.decide_batch(self.boss, b.pk, True)
        s = RW.propose_payout(self.maker, self.customer, 'ledger')
        with self.assertRaisesMessage(ValidationError, 'reason'):
            RW.decide_payout(self.boss, s.pk, False)
        RW.decide_payout(self.boss, s.pk, False, note='Dealer asked for bank transfer')
        self.assertEqual(RW.payable_dealers()[0]['due'], D('40.00'))
        force_login_recent(self.client, self.maker)
        for url in [reverse('scheme_payouts'), reverse('scheme_batch', args=[b.pk]), reverse('scheme_statement', args=[self.customer.pk])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(SchemeEntitlementBatch.objects.count(), 1)

    def test_foreign_site_cannot_prepare_decide_or_read_batch(self):
        from .models import AuditEvent
        from . import insights as I
        v = approved_scheme(self)
        invoice_fixture(self, [(self.bag, D(2000))])
        evaluation = self.calc(v)
        before = AuditEvent.objects.count()
        with self.assertRaises(PermissionDenied):
            RW.create_batch(self.outsider, evaluation.pk)
        self.assertEqual(AuditEvent.objects.count(), before)
        self.assertFalse(SchemeEntitlementBatch.objects.exists())
        batch = RW.create_batch(self.maker, evaluation.pk)
        with self.assertRaises(PermissionDenied):
            RW.decide_batch(self.outsider, batch.pk, True)
        batch.refresh_from_db()
        self.assertEqual(batch.status, 'submitted')
        force_login_recent(self.client, self.outsider)
        self.assertEqual(self.client.get(reverse('scheme_batch', args=[batch.pk])).status_code, 404)
        response = self.client.get(reverse('scheme_payouts'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(batch))
        self.assertFalse(any(x['label'] == 'Scheme entitlements to approve' for x in I.my_queue(self.outsider)))

    def test_module_grants_without_company_finance_cannot_read_or_post_rewards(self):
        from .models import SchemeSettlement, AuditEvent
        from . import insights as I
        v = approved_scheme(self)
        invoice_fixture(self, [(self.bag, D(2000))])
        evaluation = self.calc(v)
        batch = RW.create_batch(self.maker, evaluation.pk)
        RW.decide_batch(self.boss, batch.pk, True)
        payout = RW.propose_payout(self.maker, self.customer, 'ledger')
        self.checker.user_permissions.remove(Permission.objects.get(codename='access_company_finance'))
        actor = User.objects.get(pk=self.checker.pk)
        before = AuditEvent.objects.count()
        for call in [
            lambda: RW.create_batch(actor, evaluation.pk),
            lambda: RW.decide_batch(actor, batch.pk, False, 'Denied'),
            lambda: RW.propose_payout(actor, self.customer, 'ledger'),
            lambda: RW.decide_payout(actor, payout.pk, True),
        ]:
            with self.assertRaises(PermissionDenied):
                call()
        self.assertEqual(AuditEvent.objects.count(), before)
        self.assertEqual(SchemeSettlement.objects.get(pk=payout.pk).status, 'proposed')
        force_login_recent(self.client, actor)
        for url in [
            reverse('scheme_payouts'), reverse('scheme_batch', args=[batch.pk]),
            reverse('scheme_statement', args=[self.customer.pk]),
            reverse('insight_area', args=['schemes']),
        ]:
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for url in [
            reverse('scheme_batch_new', args=[evaluation.pk]),
            reverse('scheme_batch_decide', args=[batch.pk, 'approve']),
            reverse('scheme_payout_new', args=[self.customer.pk]),
            reverse('scheme_payout_decide', args=[payout.pk, 'approve']),
        ]:
            self.assertEqual(self.client.post(url).status_code, 403, url)
        labels = {x['label'] for x in I.my_queue(actor)}
        self.assertNotIn('Scheme entitlements to approve', labels)
        self.assertNotIn('Scheme payouts to approve', labels)
