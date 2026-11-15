import uuid
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.contrib.auth.models import User,Permission
from django.utils import timezone
from django.urls import reverse
from accounts.test_support import force_login_recent
from accounts import delegation as Delegation,approval_work as Work
from accounts.models import ApprovalDelegationUse
from . import test_freight as fixture
from . import logistics as L,freight as F,freight_recovery as R,pod,treasury as T,gl
from .models import Dispatch,PODCorrection,Site,SiteAccess,JournalEntry

@override_settings(EMAIL_OTP_ENABLED=False)
class PODCorrectionTests(TestCase):
    trip=fixture.FreightTests.trip
    @classmethod
    def setUpTestData(cls):fixture.FreightTests.setUpTestData.__func__(cls)

    def delivered(self,short=4,bill_first=False):
        trip=self.trip(self.truck,self.driver,100)
        bill=None
        if bill_first:
            bill=F.create_bill(self.admin,self.trans,'COR-1',timezone.localdate(),{trip:dict(claimed=trip.freight_amount,days=0,extra=0,reason='')})
            F.decide(self.checker,bill.pk,True,'Contract verified');bill.refresh_from_db()
        L.deliver(self.admin,trip.pk,'Original receiver',shortage=short,notes='Original POD',file=(b'%PDF fictional retained proof','application/pdf'))
        trip.refresh_from_db()
        return trip,bill

    def propose(self,trip,short=2,**extra):
        values=dict(received_by='Corrected receiver',receiver_mobile='',shortage=str(short),damaged='0',notes='Corrected POD')
        values.update(extra)
        return pod.propose(self.admin,trip.pk,values,'Receiver confirmed correction',uuid.uuid4(),pod.digest(pod.snapshot(trip)))

    def test_independent_correction_retains_original_and_no_ledger_mutation(self):
        trip,_=self.delivered();before=pod.snapshot(trip);count=JournalEntry.objects.count()
        item=self.propose(trip)
        trip.refresh_from_db();self.assertEqual(trip.shortage_qty,4)
        self.assertEqual(item.before,before)
        with self.assertRaisesMessage(ValidationError,'maker-checker'):pod.decide(self.admin,item.pk,'approve','Own review')
        pod.decide(self.checker,item.pk,'approve','Receiver independently contacted')
        trip.refresh_from_db();item.refresh_from_db()
        self.assertEqual((trip.shortage_qty,trip.received_by),(D(2),'Corrected receiver'))
        self.assertEqual(trip.delivered_at.isoformat(),before['delivered_at'])
        self.assertEqual(trip.pod_recorded_by_id,self.admin.pk)
        self.assertEqual(bytes(trip.pod_file),b'%PDF fictional retained proof')
        self.assertEqual(item.before['shortage_qty'],'4.000')
        self.assertEqual(item.reviewed_by_id,self.checker.pk)
        self.assertEqual(JournalEntry.objects.count(),count)
        with self.assertRaises(ValidationError):pod.decide(self.checker,item.pk,'approve','Retry')

    def test_bad_quantities_atomic_for_delivery_and_correction(self):
        for value in ['NaN','sNaN','Infinity','-1','0.0001','bad','1000000000']:
            with self.subTest(value=value):
                trip=self.trip(self.truck,self.driver,100)
                with self.assertRaises(ValidationError):L.deliver(self.admin,trip.pk,'Receiver',shortage=value,notes='Invalid')
                trip.refresh_from_db();self.assertEqual(trip.status,'gate_out');self.assertIsNone(trip.pod_recorded_by_id)
        trip,_=self.delivered()
        for value in ['NaN','-1','101','0.0001']:
            with self.assertRaises(ValidationError):self.propose(trip,value)
        self.assertFalse(PODCorrection.objects.exists())

    def test_retry_reject_cancel_and_stale_evidence(self):
        trip,_=self.delivered();item=self.propose(trip)
        values=dict(received_by=item.after['received_by'],receiver_mobile='',shortage='2',damaged=0,notes=item.after['pod_notes'])
        self.assertEqual(pod.propose(self.admin,trip.pk,values,item.reason,item.request_key,'stale').pk,item.pk)
        with self.assertRaises(ValidationError):self.propose(trip,3)
        Dispatch.objects.filter(pk=trip.pk).update(pod_notes='New observed evidence')
        with self.assertRaisesMessage(ValidationError,'evidence changed'):pod.decide(self.checker,item.pk,'approve','Verified')
        pod.decide(self.checker,item.pk,'reject','Source changed')
        trip.refresh_from_db();fresh=self.propose(trip,3);pod.decide(self.admin,fresh.pk,'cancel','Withdraw')
        trip.refresh_from_db();self.assertEqual(trip.shortage_qty,4)
        self.assertEqual(PODCorrection.objects.count(),2)

    def test_delegated_site_review_and_routing(self):
        trip,_=self.delivered()
        deputy=User.objects.create_user('pod-deputy');deputy.user_permissions.add(Permission.objects.get(codename='view_dispatch'))
        SiteAccess.objects.create(user=deputy,site=self.site)
        now=timezone.now();grant=Delegation.create(self.admin,self.checker,deputy,'approve_pod_correction',self.site,now,now+timedelta(days=1),'POD correction cover')
        item=self.propose(trip);task=Work.source('pod_correction',item.pk)
        self.assertTrue(Work.eligible(deputy,task)[0]);self.assertFalse(Work.eligible(self.admin,task)[0])
        self.assertEqual(Work.source_url(task),reverse('dispatch_detail',args=[trip.pk]))
        with patch('erp.pod.L._log',side_effect=ValidationError('Audit failure')):
            with self.assertRaises(ValidationError):pod.decide(deputy,item.pk,'approve','Verified')
        trip.refresh_from_db();item.refresh_from_db();self.assertEqual(trip.shortage_qty,4);self.assertEqual(item.status,'requested')
        self.assertFalse(ApprovalDelegationUse.objects.filter(delegation=grant).exists())
        pod.decide(deputy,item.pk,'approve','Verified')
        item.refresh_from_db();self.assertEqual(item.reviewed_by_id,deputy.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant).exists())

    def test_legacy_actor_and_former_contributor_are_makers(self):
        trip,_=self.delivered();Dispatch.objects.filter(pk=trip.pk).update(pod_recorded_by=None)
        trip.refresh_from_db();item=self.propose(trip)
        trip.refresh_from_db();self.assertIn(self.admin.pk,pod.makers(item))
        pod.decide(self.checker,item.pk,'approve','Verified')
        trip.refresh_from_db()
        third=User.objects.create_superuser('third-pod','third@example.com','Password-816!')
        values=dict(received_by='Third receiver',shortage='1',notes='Rechecked')
        next_item=pod.propose(third,trip.pk,values,'New evidence',uuid.uuid4(),pod.digest(pod.snapshot(trip)))
        with self.assertRaisesMessage(ValidationError,'maker-checker'):pod.decide(self.admin,next_item.pk,'approve','Former contributor')

    def test_other_site_and_read_only_cannot_act(self):
        trip,_=self.delivered();item=self.propose(trip)
        user=User.objects.create_user('pod-other');user.user_permissions.set(Permission.objects.filter(codename__in=['view_dispatch','pod_dispatch','approve_pod_correction']))
        other=Site.objects.create(code='OTHER',name='Other plant',kind='Plant');SiteAccess.objects.create(user=user,site=other)
        self.assertFalse(pod.can_decide(user,item))
        with self.assertRaises(PermissionDenied):pod.decide(user,item.pk,'approve','Wrong site')
        user.is_active=False;user.save();SiteAccess.objects.create(user=user,site=self.site)
        with self.assertRaises(PermissionDenied):pod.decide(user,item.pk,'approve','Disabled')

    def test_pending_correction_holds_payments_and_recovery_restores_liability(self):
        trip,bill=self.delivered(bill_first=True)
        request=R.propose(self.admin,bill.pk,timezone.localdate(),'Late POD',uuid.uuid4())
        R.decide(self.checker,request.pk,True,'Shortage verified')
        self.assertEqual(R.effective_payable(bill),7400)
        item=self.propose(trip)
        self.assertFalse(R.clear_for_payment(bill))
        self.assertEqual(T.open_items(self.trans),[])
        pod.decide(self.checker,item.pk,'approve','Reduced shortage confirmed')
        self.assertFalse(R.clear_for_payment(bill))
        restore=R.propose(self.admin,bill.pk,timezone.localdate(),'Reviewed POD correction',uuid.uuid4())
        self.assertEqual(restore.amount,-800)
        R.decide(self.checker,restore.pk,True,'Liability restoration verified')
        self.assertEqual(R.effective_payable(bill),8200)
        self.assertTrue(R.clear_for_payment(bill))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check(as_of=timezone.localdate())))
        from .closing import source_gaps
        self.assertFalse(source_gaps(timezone.localdate()))
        restore.refresh_from_db();JournalEntry.objects.filter(pk=restore.journal_id).update(status='draft')
        self.assertIn(f'freight-recovery:{restore.pk}',[row['source'] for row in source_gaps(timezone.localdate())])

    def test_web_proposal_and_independent_review(self):
        trip,_=self.delivered();force_login_recent(self.client,self.admin)
        url=reverse('dispatch_detail',args=[trip.pk]);self.assertContains(self.client.get(url),'Request POD correction')
        self.client.post(reverse('pod_correction_action',args=[trip.pk,'propose']),dict(received_by='Fixed receiver',shortage='2',damaged='0',notes='Revised POD',reason='Receiver confirmed',request_key=uuid.uuid4(),evidence=pod.digest(pod.snapshot(trip))))
        item=PODCorrection.objects.get();force_login_recent(self.client,self.checker)
        self.assertContains(self.client.get(url),'Approve POD correction')
        self.client.post(reverse('pod_correction_action',args=[trip.pk,'approve']),dict(correction=item.pk,note='Independently confirmed'))
        item.refresh_from_db();self.assertEqual(item.status,'approved')

    def test_restoration_after_credit_already_used_reopens_payable(self):
        trip,bill=self.delivered(short=0,bill_first=True)
        pay=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):9000},mode='cheque')
        T.decide(self.checker,pay.pk,True);T.release(self.checker,pay.pk,'TEST-PAID')
        increase=self.propose(trip,4);pod.decide(self.checker,increase.pk,'approve','Four missing bags confirmed')
        recovery=R.propose(self.admin,bill.pk,timezone.localdate(),'Corrected shortage',uuid.uuid4());R.decide(self.checker,recovery.pk,True,'Verified')
        other=self.trip(self.truck,self.driver,100)
        second=F.create_bill(self.admin,self.trans,'COR-2',timezone.localdate(),{other:dict(claimed=other.freight_amount,days=0,extra=0,reason='')})
        F.decide(self.checker,second.pk,True,'Verified')
        offset=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):1600,('frb',second.pk):9000},mode='cheque')
        T.decide(self.checker,offset.pk,True);T.release(self.checker,offset.pk,'TEST-OFFSET')
        trip.refresh_from_db();decrease=self.propose(trip,2);pod.decide(self.checker,decrease.pk,'approve','Two bags found')
        restore=R.propose(self.admin,bill.pk,timezone.localdate(),'Reduce recognized recovery',uuid.uuid4());R.decide(self.checker,restore.pk,True,'Verified')
        items=T.open_items(self.trans);self.assertEqual(len(items),1);self.assertEqual(items[0]['amount'],800)
        due=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):800},mode='cheque')
        T.decide(self.checker,due.pk,True);T.release(self.checker,due.pk,'TEST-RESTORED')
        self.assertFalse(T.open_items(self.trans))
        self.assertTrue(all(not row['diff'] for row in gl.subledger_check()))

    def test_pending_correction_blocks_existing_approved_payment_release(self):
        trip,bill=self.delivered(short=0,bill_first=True)
        pay=T.propose(self.admin,self.trans,gl.acct('bank_main'),{('frb',bill.pk):9000},mode='cheque')
        T.decide(self.checker,pay.pk,True)
        item=self.propose(trip,2)
        with self.assertRaisesMessage(ValidationError,'reconcile'):T.release(self.checker,pay.pk,'TEST-BLOCK')
        pod.decide(self.checker,item.pk,'reject','Receiver disproved shortage')
        T.release(self.checker,pay.pk,'TEST-UNCHANGED')

    def test_original_recorder_and_delegation_principal_excluded(self):
        trip=self.trip(self.truck,self.driver,100)
        recorder=User.objects.create_superuser('pod-recorder','recorder@example.com','Password-816!')
        L.deliver(recorder,trip.pk,'Receiver',shortage=4,notes='Original')
        trip.refresh_from_db();item=self.propose(trip)
        with self.assertRaisesMessage(ValidationError,'maker-checker'):pod.decide(recorder,item.pk,'approve','Own POD')
        deputy=User.objects.create_user('pod-proxy');deputy.user_permissions.add(Permission.objects.get(codename='view_dispatch'));SiteAccess.objects.create(user=deputy,site=self.site)
        now=timezone.now();Delegation.create(self.admin,recorder,deputy,'approve_pod_correction',self.site,now,now+timedelta(days=1),'Review cover')
        self.assertFalse(pod.can_decide(deputy,item))
        with self.assertRaises(ValidationError):pod.decide(deputy,item.pk,'approve','Proxy')

    def test_no_change_and_forged_fingerprint_and_conflicting_retry(self):
        trip,_=self.delivered()
        values=dict(received_by=trip.received_by,receiver_mobile='',shortage='4',damaged='0',notes=trip.pod_notes)
        with self.assertRaisesMessage(ValidationError,'at least one'):pod.propose(self.admin,trip.pk,values,'Nothing changed',uuid.uuid4(),pod.digest(pod.snapshot(trip)))
        values['shortage']='2'
        with self.assertRaisesMessage(ValidationError,'evidence changed'):pod.propose(self.admin,trip.pk,values,'Changed',uuid.uuid4(),'forged')
        item=self.propose(trip)
        with self.assertRaisesMessage(ValidationError,'different correction'):pod.propose(self.admin,trip.pk,values,'Conflicting request',item.request_key,item.source_digest)

    def test_recovery_chain_cannot_backdate_before_prior_approved_reduction(self):
        trip,bill=self.delivered(bill_first=True)
        req=R.propose(self.admin,bill.pk,timezone.localdate(),'Original recovery',uuid.uuid4());R.decide(self.checker,req.pk,True,'Verified')
        yesterday=timezone.localdate()-timedelta(days=1)
        # Retained earlier operational dates must not allow a later chain step before its predecessor.
        type(bill).objects.filter(pk=bill.pk).update(bill_date=yesterday)
        Dispatch.objects.filter(pk=trip.pk).update(delivered_at=timezone.now()-timedelta(days=1),shortage_qty=5)
        with self.assertRaisesMessage(ValidationError,'recovery date'):
            R.propose(self.admin,bill.pk,yesterday,'New recovery',uuid.uuid4())

    def test_initial_delivery_rejects_invalid_file_and_phone_and_expired_actor(self):
        from accounts.models import UserProfile
        trip=self.trip(self.truck,self.driver,100)
        for file in [(b'RIFFxxxxWAVE','image/webp'),(b'%PDF test','image/png'),(b'%PDF'+b'x'*(5*1024*1024),'application/pdf')]:
            with self.assertRaises(ValidationError):L.deliver(self.admin,trip.pk,'Receiver',file=file)
        with self.assertRaises(ValidationError):L.deliver(self.admin,trip.pk,'Receiver',receiver_mobile='invalid')
        UserProfile.objects.update_or_create(user=self.admin,defaults={'access_expires_at':timezone.now()-timedelta(minutes=1)})
        with self.assertRaises(PermissionDenied):L.deliver(self.admin,trip.pk,'Receiver')
        trip.refresh_from_db();self.assertEqual(trip.status,'gate_out');self.assertIsNone(trip.pod_file)

    def test_approved_nonfinancial_correction_invalidates_prior_recovery_review(self):
        trip,bill=self.delivered(bill_first=True)
        req=R.propose(self.admin,bill.pk,timezone.localdate(),'Original POD recovery',uuid.uuid4())
        values=dict(received_by=trip.received_by,receiver_mobile='9876543210',shortage='4',damaged='0',notes=trip.pod_notes)
        correction=pod.propose(self.admin,trip.pk,values,'Receiver contact corrected',uuid.uuid4(),pod.digest(pod.snapshot(trip)))
        pod.decide(self.checker,correction.pk,'approve','Contact independently verified')
        with self.assertRaisesMessage(ValidationError,'evidence changed'):R.decide(self.checker,req.pk,True,'Old evidence')
        req.refresh_from_db();self.assertEqual(req.status,'requested');self.assertIsNone(req.journal_id)
        R.decide(self.checker,req.pk,False,'Review current approved POD version')
        fresh=R.propose(self.admin,bill.pk,timezone.localdate(),'Latest POD version',uuid.uuid4())
        self.assertEqual(fresh.amount,1600)
        R.decide(self.checker,fresh.pk,True,'Current evidence confirmed')
