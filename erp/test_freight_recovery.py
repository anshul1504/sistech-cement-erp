import uuid
from decimal import Decimal as D
from datetime import timedelta
from django.test import TestCase, override_settings
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.urls import reverse
from accounts.test_support import force_login_recent
from . import test_freight as fixture
from . import freight as F, freight_recovery as R, logistics as L, treasury as T, gl
from .models import FreightRecoveryAdjustment, Dispatch

@override_settings(EMAIL_OTP_ENABLED=False)
class RecoveryTests(TestCase):
    trip=fixture.FreightTests.trip
    @classmethod
    def setUpTestData(cls):
        fixture.FreightTests.setUpTestData.__func__(cls)

    def bill(self, reference='REC-1'):
        trip=self.trip(self.truck,self.driver,100)
        bill=F.create_bill(self.admin,self.trans,reference,timezone.localdate(),{trip:{'claimed':trip.freight_amount,'days':0,'extra':D(0),'reason':''}})
        F.decide(self.checker,bill.pk,True,'Verified')
        bill.refresh_from_db()
        return bill,trip

    def shortage(self,trip,qty=4):
        L.deliver(self.admin,trip.pk,'Receiver',shortage=qty,notes='POD verified shortage')

    def request(self,bill):
        return R.propose(self.admin,bill.pk,timezone.localdate(),'POD shortage',uuid.uuid4(),R.basis(bill)['digest'])

    def payment(self,bill,amount=9000):
        return T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):amount},mode='cheque')

    def test_unpaid_recovery_preserves_bill_and_posts_once(self):
        bill,trip=self.bill();snapshot=bill.review_snapshot
        self.shortage(trip)
        self.assertFalse(R.clear_for_payment(bill))
        req=self.request(bill)
        self.assertEqual(req.amount,D(1600));self.assertIsNone(req.journal_id)
        with self.assertRaisesMessage(ValidationError,'maker-checker'):R.decide(self.admin,req.pk,True,'Own review')
        R.decide(self.checker,req.pk,True,'POD independently confirmed')
        req.refresh_from_db();bill.refresh_from_db()
        self.assertIsNotNone(req.journal_id)
        self.assertEqual((bill.payable,bill.deductions,bill.review_snapshot),(D(9000),D(0),snapshot))
        self.assertEqual(R.effective_payable(bill),D(7400))
        self.assertTrue(R.clear_for_payment(bill))
        self.assertEqual(T.open_items(self.trans)[0]['amount'],D(7400))
        with self.assertRaises(ValidationError):R.decide(self.checker,req.pk,True,'Duplicate')

    def test_paid_recovery_becomes_signed_credit_offset(self):
        bill,trip=self.bill();pay=self.payment(bill)
        T.decide(self.checker,pay.pk,True);T.release(self.checker,pay.pk,'TEST-001')
        self.shortage(trip);req=self.request(bill);R.decide(self.checker,req.pk,True,'Verified POD')
        self.assertEqual(T.open_items(self.trans)[0]['amount'],D(-1600))
        next_bill,_=self.bill('REC-2')
        force_login_recent(self.client,self.admin)
        self.assertContains(self.client.get(reverse('payment_new',args=[self.trans.pk])),'Freight recovery credit')
        offset=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):1600,('frb',next_bill.pk):9000},mode='cheque')
        self.assertEqual(offset.amount,D(7400))
        self.assertEqual(offset.allocations.get(freight_bill=bill).amount,D(-1600))
        T.decide(self.checker,offset.pk,True);T.release(self.checker,offset.pk,'TEST-002')
        self.assertEqual(T.open_items(self.trans),[])
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check(as_of=timezone.localdate())))
        from .closing import source_gaps
        self.assertFalse(source_gaps(timezone.localdate()))

    def test_pending_payment_and_recovery_require_explicit_cancel(self):
        bill,trip=self.bill();pay=self.payment(bill);T.decide(self.checker,pay.pk,True)
        self.shortage(trip);req=self.request(bill)
        with self.assertRaisesMessage(ValidationError,'reconcile'):T.release(self.checker,pay.pk,'TEST-BLOCK')
        with self.assertRaisesMessage(ValidationError,'pending payment'):R.decide(self.checker,req.pk,True,'Verified')
        req.refresh_from_db();self.assertEqual(req.status,'requested');self.assertIsNone(req.journal_id)
        T.decide(self.checker,pay.pk,False,'Reallocate using recovery')
        R.decide(self.checker,req.pk,True,'Verified')

    def test_stale_source_rejected_and_request_can_be_replaced(self):
        bill,trip=self.bill();self.shortage(trip);req=self.request(bill)
        Dispatch.objects.filter(pk=trip.pk).update(shortage_qty=5)
        with self.assertRaisesMessage(ValidationError,'evidence changed'):R.decide(self.checker,req.pk,True,'Verified')
        R.decide(self.checker,req.pk,False,'POD changed')
        fresh=self.request(bill);self.assertEqual(fresh.amount,D(2000))
        R.decide(self.checker,fresh.pk,True,'Rechecked')

    def test_request_retry_cancel_and_date_guards(self):
        bill,trip=self.bill();self.shortage(trip);req=self.request(bill)
        retry=R.propose(self.admin,bill.pk,req.on,req.reason,req.request_key)
        self.assertEqual(req.pk,retry.pk)
        with self.assertRaises(ValidationError):R.propose(self.admin,bill.pk,req.on,'Different reason',req.request_key)
        R.cancel(self.admin,req.pk,'New evidence required')
        with self.assertRaisesMessage(ValidationError,'recovery date'):
            R.propose(self.admin,bill.pk,timezone.localdate()-timedelta(days=1),'POD',uuid.uuid4())
        self.assertEqual(FreightRecoveryAdjustment.objects.count(),1)

    def test_failed_original_payment_invalidates_reserved_credit(self):
        bill,trip=self.bill();pay=self.payment(bill)
        T.decide(self.checker,pay.pk,True);T.release(self.checker,pay.pk,'TEST-001')
        self.shortage(trip);req=self.request(bill);R.decide(self.checker,req.pk,True,'Verified')
        next_bill,_=self.bill('REC-2')
        force_login_recent(self.client,self.admin)
        self.assertContains(self.client.get(reverse('payment_new',args=[self.trans.pk])),'Freight recovery credit')
        offset=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):1600,('frb',next_bill.pk):9000},mode='cheque')
        T.decide(self.checker,offset.pk,True)
        T.fail(self.checker,pay.pk,'Bank returned cheque')
        with self.assertRaisesMessage(ValidationError,'no longer backed'):T.release(self.checker,offset.pk,'TEST-BLOCK')

    def test_recovery_forms_and_review(self):
        bill,trip=self.bill();self.shortage(trip)
        force_login_recent(self.client,self.admin)
        url=reverse('freight_bill_detail',args=[bill.pk])
        page=self.client.get(url);self.assertContains(page,'Request recovery adjustment')
        response=self.client.post(reverse('freight_recovery_action',args=[bill.pk,'propose']),{'date':timezone.localdate(),'reason':'POD shortage','request_key':uuid.uuid4(),'evidence':R.basis(bill)['digest']})
        self.assertRedirects(response,url)
        req=FreightRecoveryAdjustment.objects.get()
        force_login_recent(self.client,self.checker)
        self.assertContains(self.client.get(url),'Approve recovery')
        self.client.post(reverse('freight_recovery_action',args=[bill.pk,'approve']),{'recovery':req.pk,'note':'POD independently verified'})
        req.refresh_from_db();self.assertEqual(req.status,'approved')

    def test_delegated_review_rollback_and_actor(self):
        from unittest.mock import patch
        from django.contrib.auth.models import User,Permission
        from accounts import delegation as Delegation
        from accounts.models import ApprovalDelegationUse
        deputy=User.objects.create_user('recovery-deputy')
        deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_freightbill','access_company_finance']))
        now=timezone.now()
        grant=Delegation.create(self.admin,self.checker,deputy,'approve_freightbill',None,now,now+timedelta(days=1),'Recovery review cover')
        bill,trip=self.bill();self.shortage(trip);req=self.request(bill)
        from accounts import approval_work as Work
        task=Work.source('freight_recovery',req.pk)
        self.assertTrue(Work.eligible(deputy,task)[0])
        self.assertFalse(Work.eligible(self.admin,task)[0])
        self.assertEqual(Work.source_url(task),reverse('freight_bill_detail',args=[bill.pk]))
        with patch('erp.freight_recovery.gl.post',side_effect=ValidationError('Closed period')):
            with self.assertRaisesMessage(ValidationError,'Closed period'):R.decide(deputy,req.pk,True,'Verified')
        req.refresh_from_db();self.assertEqual(req.status,'requested');self.assertIsNone(req.journal_id)
        self.assertFalse(ApprovalDelegationUse.objects.filter(delegation=grant).exists())
        R.decide(deputy,req.pk,True,'POD independently verified')
        req.refresh_from_db();self.assertEqual(req.reviewed_by_id,deputy.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=deputy).exists())

    def test_source_fingerprint_and_finance_access_required(self):
        from django.contrib.auth.models import User,Permission
        from django.core.exceptions import PermissionDenied
        bill,trip=self.bill();self.shortage(trip)
        with self.assertRaisesMessage(ValidationError,'evidence changed'):
            R.propose(self.admin,bill.pk,timezone.localdate(),'POD',uuid.uuid4(),'stale')
        local=User.objects.create_user('recovery-local')
        local.user_permissions.set(Permission.objects.filter(codename__in=['view_freightbill','add_freightbill','approve_freightbill']))
        with self.assertRaises(PermissionDenied):R.propose(local,bill.pk,timezone.localdate(),'POD',uuid.uuid4())
        self.assertFalse(FreightRecoveryAdjustment.objects.exists())

    def test_zero_payable_floor_retains_recovery_evidence_without_journal(self):
        bill,trip=self.bill()
        # A historical fully advanced claim has no residual liability to reduce.
        type(bill).objects.filter(pk=bill.pk).update(advance=9000,payable=0)
        bill.refresh_from_db();self.shortage(trip)
        req=self.request(bill);self.assertEqual(req.amount,0)
        R.decide(self.checker,req.pk,True,'Recovery acknowledged against advance floor')
        req.refresh_from_db();self.assertIsNone(req.journal_id)
        self.assertTrue(R.clear_for_payment(bill))
        Dispatch.objects.filter(pk=trip.pk).update(shortage_qty=3)
        self.assertTrue(R.basis(bill)['changed'])
        self.assertEqual(R.basis(bill)['amount'],0)

    def test_historical_cutoff_and_missing_recovery_posting(self):
        from .models import JournalEntry
        from .closing import historical_checks,source_gaps
        bill,trip=self.bill();yesterday=timezone.localdate()-timedelta(days=1)
        type(bill).objects.filter(pk=bill.pk).update(bill_date=yesterday)
        JournalEntry.objects.filter(source=f'frb:{bill.pk}').update(date=yesterday)
        bill.refresh_from_db();self.shortage(trip);req=self.request(bill)
        R.decide(self.checker,req.pk,True,'Independent POD review')
        before=next(row for row in historical_checks(yesterday) if row['name']=='Transporter payables')
        current=next(row for row in historical_checks(timezone.localdate()) if row['name']=='Transporter payables')
        self.assertEqual((before['sub'],before['diff']),(D(9000),0))
        self.assertEqual((current['sub'],current['diff']),(D(7400),0))
        req.refresh_from_db();JournalEntry.objects.filter(pk=req.journal_id).update(status='draft')
        self.assertIn(f'freight-recovery:{req.pk}',[row['source'] for row in source_gaps(timezone.localdate())])
