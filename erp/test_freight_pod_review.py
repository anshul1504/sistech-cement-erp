"""Late delivery evidence cannot silently bypass freight recovery before approval."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import User,Permission
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts import delegation as D
from accounts.models import ApprovalDelegationUse
from accounts.test_support import force_login_recent
from . import freight as F,logistics as L,test_freight as fixture
from .models import FreightBill,JournalEntry


@override_settings(EMAIL_OTP_ENABLED=False)
class FreightPODReviewTests(TestCase):
    trip=fixture.FreightTests.trip

    @classmethod
    def setUpTestData(cls):fixture.FreightTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.dispatch=self.trip(self.truck,self.driver,100)
        self.bill=self.claim('POD-PENDING')
        self.deputy=User.objects.create_user('pod-freight-deputy')
        self.deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_freightbill','access_company_finance']))
        now=timezone.now();D.create(self.admin,self.checker,self.deputy,'approve_freightbill',None,now,now+timedelta(days=1),'Independent POD recovery review')

    def claim(self,reference):
        return F.create_bill(self.admin,self.trans,reference,timezone.localdate(),{self.dispatch:dict(claimed=self.dispatch.freight_amount,extra=0,days=0,reason='')})

    def deliver(self,shortage=0,damaged=0):
        return L.deliver(self.admin,self.dispatch.pk,'Fictional receiver',shortage=shortage,damaged=damaged,notes='Retained POD findings')

    def test_late_shortage_blocks_approval_without_rewriting_claim_or_posting(self):
        before=self.bill.payable;token=F.review_token(self.bill);self.deliver(shortage=4)
        for expected in [token,F.review_token(FreightBill.objects.get(pk=self.bill.pk)),None]:
            with self.assertRaises(ValidationError):F.decide(self.deputy,self.bill.pk,True,'Cannot use stale zero recovery',expected_evidence=expected)
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.status,'submitted');self.assertEqual(self.bill.payable,before)
        self.assertEqual(self.bill.lines.get().shortage_recovery,0)
        self.assertFalse(ApprovalDelegationUse.objects.exists());self.assertFalse(JournalEntry.objects.filter(source=f'frb:{self.bill.pk}').exists())

    def test_reject_and_correct_uses_current_recovery_and_keeps_old_source_evidence(self):
        self.deliver(shortage=4)
        current=F.review_token(self.bill)
        F.decide(self.deputy,self.bill.pk,False,'Reject stale recovery after POD',expected_evidence=current)
        self.bill.refresh_from_db()
        source=self.bill.review_snapshot['source_evidence']['trips'][0]
        self.assertEqual(source['recorded_recovery'],'0.00');self.assertEqual(source['current_recovery'],'1600.00')
        corrected=self.claim('POD-CORRECTED')
        self.assertEqual(corrected.lines.get().shortage_recovery,Decimal(1600))
        self.assertEqual(corrected.payable,Decimal(7400))
        F.decide(self.deputy,corrected.pk,True,'Current shortage and deduction independently reviewed',expected_evidence=F.review_token(corrected))
        self.assertEqual(JournalEntry.objects.get(source=f'frb:{corrected.pk}').total,Decimal(7400))
        self.assertEqual(ApprovalDelegationUse.objects.count(),2)
        self.bill.refresh_from_db();self.assertEqual(self.bill.review_snapshot['source_evidence']['trips'][0],source)

    def test_pod_without_recovery_change_requires_fresh_web_evidence_but_not_reentry(self):
        old=F.review_token(self.bill);self.deliver()
        with self.assertRaises(ValidationError):F.decide(self.deputy,self.bill.pk,True,'Stale visible POD',expected_evidence=old)
        fresh=F.review_token(FreightBill.objects.get(pk=self.bill.pk))
        self.assertNotEqual(old,fresh)
        F.decide(self.deputy,self.bill.pk,True,'Fresh no-shortage POD reviewed',expected_evidence=fresh)
        self.bill.refresh_from_db();self.assertEqual(self.bill.payable,Decimal(9000));self.assertEqual(self.bill.checked_by_id,self.deputy.pk)
        self.assertEqual(self.bill.review_snapshot['source_evidence']['trips'][0]['receiver'],'Fictional receiver')

    def test_changed_header_calculation_cannot_be_approved(self):
        FreightBill.objects.filter(pk=self.bill.pk).update(payable=self.bill.payable+100)
        with self.assertRaises(ValidationError):F.decide(self.deputy,self.bill.pk,True,'Totals inconsistent')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.assertFalse(JournalEntry.objects.filter(source=f'frb:{self.bill.pk}').exists())

    def test_web_hides_approval_for_stale_recovery_but_allows_independent_rejection(self):
        self.deliver(damaged=2);force_login_recent(self.client,self.deputy)
        response=self.client.get(reverse('freight_bill_detail',args=[self.bill.pk]))
        self.assertContains(response,'current POD recovery');self.assertNotContains(response,'>Approve payable</button>')
        self.assertContains(response,'>Reject</button>')
        token=response.context['review_token']
        response=self.client.post(reverse('freight_bill_decide',args=[self.bill.pk,'reject']),{'note':'Reject mismatched recovery','evidence':token})
        self.assertEqual(response.status_code,302);self.bill.refresh_from_db();self.assertEqual(self.bill.status,'rejected')

    def test_web_cannot_bypass_review_by_omitting_fingerprint(self):
        force_login_recent(self.client,self.deputy)
        response=self.client.post(reverse('freight_bill_decide',args=[self.bill.pk,'approve']),{'note':'Missing evidence proof'})
        self.assertEqual(response.status_code,302);self.bill.refresh_from_db();self.assertEqual(self.bill.status,'submitted')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_valid_initial_claim_approval_keeps_frozen_numbers(self):
        before=(self.bill.payable,self.bill.contracted,self.bill.claimed)
        token=F.review_token(self.bill)
        F.decide(self.deputy,self.bill.pk,True,'Initial reviewed evidence',expected_evidence=token)
        self.bill.refresh_from_db();self.assertEqual((self.bill.payable,self.bill.contracted,self.bill.claimed),before)
        self.assertEqual(self.bill.review_snapshot['evidence_digest'],token)
        self.assertEqual(len(self.bill.review_snapshot['evidence_digest']),64)

    def test_changed_contract_cannot_be_approved_with_old_claim_amount(self):
        from .models import Dispatch
        Dispatch.objects.filter(pk=self.dispatch.pk).update(freight_amount=10000)
        with self.assertRaises(ValidationError):F.decide(self.deputy,self.bill.pk,True,'Contract changed')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.bill.refresh_from_db();self.assertEqual(self.bill.status,'submitted')

    def test_changed_extra_cap_requires_a_corrected_pending_claim(self):
        from .models import FreightRate
        F.decide(self.checker,self.bill.pk,False,'Prepare capped extra-charge claim')
        bill=F.create_bill(self.admin,self.trans,'EXTRA-CAP-CLAIM',timezone.localdate(),{
            self.dispatch:dict(claimed=9000,extra=3000,days=2,reason='Detention evidence')})
        self.assertEqual(bill.lines.get().allowed_extra,2500)
        FreightRate.objects.filter(pk=self.dispatch.freight_rate_id).update(detention_per_day=0)
        with self.assertRaises(ValidationError):F.decide(self.deputy,bill.pk,True,'Cap evidence changed')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.assertFalse(JournalEntry.objects.filter(source=f'frb:{bill.pk}').exists())

    def test_missing_invoice_quantities_cannot_turn_shortage_into_zero_recovery(self):
        self.dispatch.invoice.lines.update(quantity=0)
        self.deliver(shortage=4)
        with self.assertRaisesMessage(ValidationError,'linked invoice quantities'):
            F.decide(self.deputy,self.bill.pk,True,'Do not silently waive missing recovery basis')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.bill.refresh_from_db();self.assertEqual(self.bill.status,'submitted')
