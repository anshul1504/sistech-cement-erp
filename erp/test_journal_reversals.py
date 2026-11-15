"""Manual posted corrections require independent retained requests and atomic posting."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth.models import User,Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts import delegation as D,approval_work as AW,work_handoff as H
from accounts.models import ApprovalDelegationUse,ApprovalRouting,UserProfile
from accounts.test_support import force_login_recent
from . import gl,journal_reversals as JR
from .models import JournalEntry,JournalReversalRequest,FinancialPeriod
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class JournalReversalTests(TestCase):
    def setUp(self):
        seed_business_data(self);self.today=timezone.localdate()
        self.admin=User.objects.create_superuser('reversal-admin','qa@example.com','Fictional-password-816!')
        self.deputy=User.objects.create_user('reversal-deputy');self.deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_journalentry','access_company_finance']))
        self.entry=gl.create_voucher(self.maker,self.today,self.site,'Fictional power accrual',[(gl.acct('power'),Decimal(100),0,None,''),(gl.acct('bank_main'),0,Decimal(100),None,'')])
        gl.decide_voucher(self.checker,self.entry.pk,True,'Independent source approval');self.entry.refresh_from_db()

    def request(self,key=None,user=None):return JR.propose(user or self.maker,self.entry.pk,self.today,'Duplicate accrual correction',key or uuid4())

    def grant(self,principal=None):
        now=timezone.now()
        return D.create(self.admin,principal or self.checker,self.deputy,'approve_journalentry',None,now,now+timedelta(days=1),'Independent reversal review cover')

    def test_request_has_no_posting_and_repeated_key_retains_same_receipt(self):
        key=uuid4();before=list(self.entry.lines.values());request=self.request(key)
        self.assertEqual(self.request(key).pk,request.pk)
        self.assertEqual(JournalReversalRequest.objects.count(),1);self.assertEqual(JournalEntry.objects.count(),1)
        self.assertEqual(list(self.entry.lines.values()),before)
        with self.assertRaises(ValidationError):JR.propose(self.maker,self.entry.pk,self.today,'Changed reason',key)
        with self.assertRaises(ValidationError):self.request()

    def test_independent_approval_posts_one_balanced_linked_reversal(self):
        request=self.request();before=list(self.entry.lines.values())
        result=JR.decide(self.checker,request.pk,True,'Source and correction independently reviewed')
        reversal=result.reversal
        self.assertEqual((reversal.reversal_of_id,reversal.created_by_id,reversal.approved_by_id),(self.entry.pk,self.maker.pk,self.checker.pk))
        self.assertEqual(reversal.total,self.entry.total)
        for original in self.entry.lines.all():
            line=reversal.lines.get(account=original.account)
            self.assertEqual((line.debit,line.credit),(original.credit,original.debit))
        self.assertEqual(list(self.entry.lines.values()),before)
        with self.assertRaises(ValidationError):JR.decide(self.checker,request.pk,True,'Replay denied')
        self.assertEqual(JournalEntry.objects.filter(reversal_of=self.entry).count(),1)

    def test_requester_and_original_maker_cannot_decide_or_lend_authority(self):
        request=self.request(user=self.admin)
        for actor in [self.admin,self.maker]:
            with self.assertRaises(ValidationError):JR.decide(actor,request.pk,True,'Self review denied')
        self.grant(self.maker)
        self.assertFalse(JR.can_decide(self.deputy,request))
        with self.assertRaises((PermissionDenied,ValidationError)):JR.decide(self.deputy,request.pk,True,'Maker proxy denied')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_delegated_reviewer_is_retained_and_source_stage_leaves_inbox(self):
        request=self.request();grant=self.grant()
        task=AW.source('journal_reversal',request.pk)
        self.assertEqual(AW.source_url(task),reverse('journal_detail',args=[self.entry.pk]))
        H.assign_approval(self.admin,'journal_reversal',request.pk,self.deputy,'Accountable independent reversal checker',uuid4())
        result=JR.decide(self.deputy,request.pk,True,'Independent delegated correction approval')
        self.assertEqual(result.reviewed_by_id,self.deputy.pk);self.assertEqual(result.reversal.approved_by_id,self.deputy.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=self.deputy,reference=f'REVREQ-{request.pk:05d}').exists())
        self.assertIsNone(AW.source('journal_reversal',request.pk));self.assertEqual(H.assigned_pending(self.deputy),[])
        self.assertFalse(self.deputy.has_perm('erp.approve_journalentry'))

    def test_closed_period_after_request_rolls_back_use_and_posting(self):
        request=self.request();self.grant()
        period=gl.period_for(self.today);FinancialPeriod.objects.filter(pk=period.pk).update(status='closed')
        with self.assertRaises(ValidationError):JR.decide(self.deputy,request.pk,True,'Period closed after proposal')
        request.refresh_from_db();self.assertEqual(request.status,'requested');self.assertIsNone(request.reversal_id)
        self.assertFalse(ApprovalDelegationUse.objects.exists());self.assertEqual(JournalEntry.objects.count(),1)

    def test_rejection_and_cancellation_allow_new_request_without_erasing_history(self):
        request=self.request();JR.decide(self.checker,request.pk,False,'Correction evidence insufficient')
        second=self.request();JR.cancel(self.maker,second.pk,'Withdraw before posting')
        third=self.request()
        self.assertEqual(JournalReversalRequest.objects.count(),3);self.assertEqual(JournalEntry.objects.count(),1)
        self.assertEqual(third.status,'requested')
        request.refresh_from_db();second.refresh_from_db()
        self.assertEqual((request.status,second.status),('rejected','cancelled'))

    def test_engine_manual_bypass_and_non_original_sources_are_rejected(self):
        with self.assertRaises(ValidationError):gl.reverse(self.admin,self.entry,reason='Manual bypass')
        auto=gl.post(self.admin,'TEST-SYSTEM-POST',self.today,None,'Automatic source',[(gl.acct('power'),Decimal(20),0,None,''),(gl.acct('bank_main'),0,Decimal(20),None,'')])
        with self.assertRaises(ValidationError):JR.propose(self.maker,auto.pk,self.today,'Use source correction',uuid4())
        request=self.request();reversal=JR.decide(self.checker,request.pk,True,'Approved original correction').reversal
        with self.assertRaises(ValidationError):JR.propose(self.maker,reversal.pk,self.today,'Cannot reverse reversal through manual request',uuid4())

    def test_source_date_finance_scope_and_expired_accounts_are_enforced(self):
        with self.assertRaises(ValidationError):JR.propose(self.maker,self.entry.pk,self.today-timedelta(days=1),'Backdated before source',uuid4())
        limited=User.objects.create_user('reversal-limited');limited.user_permissions.set(Permission.objects.filter(codename__in=['view_journalentry','add_journalentry','approve_journalentry']))
        with self.assertRaises(PermissionDenied):JR.propose(limited,self.entry.pk,self.today,'Missing company books scope',uuid4())
        UserProfile.objects.update_or_create(user=self.maker,defaults={'access_expires_at':timezone.now()-timedelta(seconds=1)})
        with self.assertRaises(PermissionDenied):self.request()

    def test_qualified_backup_handoff_does_not_post_a_reversal(self):
        request=self.request()
        H.assign_approval(self.admin,'journal_reversal',request.pk,self.checker,'Named reversal checker',uuid4())
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':self.admin})
        with self.captureOnCommitCallbacks(execute=True):
            self.checker.is_active=False;self.checker.save(update_fields=['is_active'])
        self.assertEqual(ApprovalRouting.objects.get(kind='journal_reversal',object_id=request.pk).assignee_id,self.admin.pk)
        request.refresh_from_db();self.assertEqual(request.status,'requested');self.assertEqual(JournalEntry.objects.count(),1)

    def test_web_request_needs_confirmation_and_decision_is_source_bound(self):
        self.client.force_login(self.maker);url=reverse('journal_action',args=[self.entry.pk,'reverse'])
        response=self.client.post(url,{'reason':'Request'})
        self.assertIn(reverse('reauthenticate'),response.url)
        force_login_recent(self.client,self.maker)
        response=self.client.post(url,{'reason':'Reviewed request','date':str(self.today),'request_key':str(uuid4())})
        self.assertEqual(response.status_code,302);self.assertEqual(JournalEntry.objects.count(),1)
        request=JournalReversalRequest.objects.get()
        other=gl.create_voucher(self.maker,self.today,None,'Other source',[(gl.acct('power'),Decimal(20),0,None,''),(gl.acct('bank_main'),0,Decimal(20),None,'')])
        force_login_recent(self.client,self.checker)
        response=self.client.post(reverse('journal_action',args=[other.pk,'approve_reversal']),{'reversal_request':request.pk,'note':'Wrong source'})
        self.assertEqual(response.status_code,404)
        request.refresh_from_db();self.assertEqual(request.status,'requested')
