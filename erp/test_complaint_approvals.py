"""Scoped complaint/CAPA routing and delegated independent decisions preserve evidence."""
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
from . import complaints as CM
from .models import SiteAccess,Complaint,CapaAction,CreditNote
from .test_support import seed_business_data
from .test_schemes import invoice_fixture


@override_settings(EMAIL_OTP_ENABLED=False)
class ComplaintApprovalTests(TestCase):
    def setUp(self):
        seed_business_data(self);self.today=timezone.localdate()
        self.inv=invoice_fixture(self,[(self.bag,Decimal(100))])
        self.admin=User.objects.create_superuser('case-admin','qa@example.com','Fictional-password-836!')
        self.reviewer=User.objects.create_user('case-principal')
        self.reviewer.user_permissions.set(Permission.objects.filter(content_type__app_label='erp'));SiteAccess.objects.create(user=self.reviewer,site=self.site)
        self.deputy=User.objects.create_user('case-deputy')
        self.deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_complaint','view_capaaction']))
        SiteAccess.objects.create(user=self.deputy,site=self.site)
        self.case=CM.log(self.maker,self.customer,'quality','Fictional quality claim',self.inv,quantity=10)
        CM.investigate(self.checker,self.case.pk,'Retained investigation findings')
        self.case.refresh_from_db()

    def grant(self,permission='decide_complaint',principal=None):
        now=timezone.now()
        return D.create(self.admin,principal or self.reviewer,self.deputy,permission,self.site,now,now+timedelta(days=1),'Independent case review cover')

    def capa(self,complaint=True):
        x=CM.add_capa(self.maker if complaint else self.reviewer,self.case if complaint else None,'Fictional corrective action','Root cause','Corrective action',self.checker,self.today+timedelta(days=2))
        CM.implement(self.maker if complaint else self.checker,x.pk,'Actual implementation by different staff member')
        x.refresh_from_db();return x

    def test_delegated_decision_and_closure_keep_actual_reviewer_and_use_history(self):
        self.grant()
        CM.decide(self.deputy,self.case.pk,'accept',5,'none','Independent evidence review')
        self.case.refresh_from_db();self.assertEqual(self.case.decided_by_id,self.deputy.pk)
        self.assertIsNotNone(AW.source('complaint_closure',self.case.pk))
        with self.assertRaises(ValidationError):CM.close(self.deputy,self.case.pk)
        self.assertEqual(ApprovalDelegationUse.objects.count(),1)
        CM.close(self.deputy,self.case.pk,'No process failure; documented storage advice sufficient')
        self.case.refresh_from_db();self.assertEqual(self.case.closed_by_id,self.deputy.pk)
        self.assertEqual(self.case.status,'closed');self.assertEqual(ApprovalDelegationUse.objects.count(),2)
        self.assertFalse(self.deputy.has_perm('erp.decide_complaint'))
        with self.assertRaises(ValidationError):CM.close(self.deputy,self.case.pk)
        self.assertEqual(ApprovalDelegationUse.objects.count(),2)

    def test_capa_verification_and_reopen_track_actual_independent_deputy(self):
        x=self.capa();self.grant('verify_capaaction')
        task=AW.source('capa_verification',x.pk)
        self.assertEqual(AW.source_url(task),reverse('capa_detail',args=[x.pk]));self.assertTrue(AW.eligible(self.deputy,task)[0])
        CM.verify(self.deputy,x.pk,False,'Effectiveness evidence failed')
        x.refresh_from_db();self.assertEqual(x.status,'reopened');self.assertEqual(x.verified_by_id,self.deputy.pk)
        self.assertIsNone(AW.source('capa_verification',x.pk))
        CM.implement(self.checker,x.pk,'Additional corrective action')
        CM.verify(self.deputy,x.pk,True,'No repeat in reviewed test evidence')
        x.refresh_from_db();self.assertEqual(x.status,'effective')
        self.assertEqual(ApprovalDelegationUse.objects.filter(delegation__permission='verify_capaaction').count(),2)

    def test_investigator_principal_and_actual_implementers_cannot_verify_by_proxy(self):
        self.grant(principal=self.checker)
        self.assertFalse(CM.can_decide(self.deputy,self.case))
        with self.assertRaises((PermissionDenied,ValidationError)):CM.decide(self.deputy,self.case.pk,'reject',0,'none','Investigator proxy denied')
        x=self.capa();self.grant('verify_capaaction',self.maker)
        self.assertFalse(CM.can_verify(self.deputy,x))
        with self.assertRaises((PermissionDenied,ValidationError)):CM.verify(self.deputy,x.pk,True,'Implementer proxy denied')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_former_assignment_owner_is_retained_across_investigation_owner_change(self):
        case=CM.log(self.maker,self.customer,'quality','Assigned before investigation',self.inv,owner=self.reviewer)
        CM.investigate(self.checker,case.pk,'New investigator and owner',owner=self.checker)
        case.refresh_from_db();self.assertIn(self.reviewer.pk,CM.complaint_makers(case))
        with self.assertRaises(ValidationError):CM.decide(self.reviewer,case.pk,'reject',0,'none','Former owner denied')
        CM.decide(self.admin,case.pk,'reject',0,'none','Independent reviewer')
        with self.assertRaises(ValidationError):CM.close(self.reviewer,case.pk)

    def test_quantity_and_effectiveness_validation_roll_back_delegation(self):
        self.grant()
        for quantity in [-1,Decimal('NaN'),Decimal('Infinity'),11]:
            with self.assertRaises(ValidationError):CM.decide(self.deputy,self.case.pk,'accept',quantity,'none','Invalid quantity')
        x=self.capa();self.grant('verify_capaaction')
        with self.assertRaises(ValidationError):CM.verify(self.deputy,x.pk,True,'')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        x.refresh_from_db();self.assertEqual(x.status,'done')

    def test_closure_queue_waits_for_settlement_and_effective_capa(self):
        CM.decide(self.reviewer,self.case.pk,'accept',5,'credit','Credit resolution')
        self.assertIsNone(AW.source('complaint_closure',self.case.pk))
        cn=CreditNote.objects.create(number='CASE-CN-QA',invoice=self.inv,partner=self.customer,reason='quality',taxable=50,cgst=0,sgst=0,igst=0,round_off=0,total=50,created_by=self.maker)
        CM.settle(self.maker,self.case.pk,credit_note=cn)
        x=self.capa()
        self.assertIsNone(AW.source('complaint_closure',self.case.pk))
        CM.verify(self.reviewer,x.pk,True,'Independent effectiveness evidence')
        self.assertIsNotNone(AW.source('complaint_closure',self.case.pk))

    def test_site_scope_expiry_and_unlinked_privacy_cannot_be_bypassed(self):
        grant=self.grant()
        from unittest.mock import patch
        with patch('accounts.delegation.timezone.now',return_value=grant.ends_at):
            with self.assertRaises(PermissionDenied):CM.decide(self.deputy,self.case.pk,'reject',0,'none','Expired authority')
        SiteAccess.objects.filter(user=self.deputy,site=self.site).delete()
        with self.assertRaises(PermissionDenied):CM.decide(self.deputy,self.case.pk,'reject',0,'none','Site removed')
        SiteAccess.objects.create(user=self.deputy,site=self.site)
        private=CM.log(self.maker,self.customer,'quality','Private unlinked case')
        CM.investigate(self.maker,private.pk,'Private investigation')
        with self.assertRaises(PermissionDenied):CM.decide(self.deputy,private.pk,'reject',0,'none','Site grant cannot read private case')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_standalone_capa_retains_personal_scope_and_direct_reviewer(self):
        x=self.capa(complaint=False)
        task=AW.source('capa_verification',x.pk)
        self.assertFalse(AW.eligible(self.reviewer,task)[0])
        self.assertTrue(AW.eligible(self.admin,task)[0])
        self.assertEqual(task['scope_label'],'Private record access')
        self.assertFalse(AW.eligible(self.deputy,task)[0])
        force_login_recent(self.client,self.reviewer)
        self.assertContains(self.client.get(reverse('capa_detail',args=[x.pk])),'Standalone action')
        with self.assertRaises(ValidationError):CM.verify(self.reviewer,x.pk,True,'Creator cannot verify')
        CM.verify(self.admin,x.pk,True,'Independent authorized review preserving private scope')

    def test_qualified_backup_handoff_moves_review_only(self):
        H.assign_approval(self.admin,'complaint_decision',self.case.pk,self.reviewer,'Accountable independent reviewer',uuid4())
        backup=User.objects.create_superuser('case-backup','backup@example.com','Fictional-password-997!')
        UserProfile.objects.update_or_create(user=self.reviewer,defaults={'work_backup':backup})
        with self.captureOnCommitCallbacks(execute=True):
            self.reviewer.is_active=False;self.reviewer.save(update_fields=['is_active'])
        self.assertEqual(ApprovalRouting.objects.get(kind='complaint_decision',object_id=self.case.pk).assignee_id,backup.pk)
        self.case.refresh_from_db();self.assertEqual(self.case.status,'investigating')
        self.assertEqual(len(H.assigned_pending(backup)),1)
        CM.decide(backup,self.case.pk,'reject',0,'none','Reviewed backup decision')
        self.assertEqual(H.assigned_pending(backup),[])

    def test_review_writes_require_recent_identity_confirmation(self):
        x=self.capa();self.client.force_login(self.reviewer)
        for url in [reverse('complaint_action',args=[self.case.pk,'decide']),reverse('complaint_action',args=[self.case.pk,'close']),reverse('capa_action',args=[x.pk,'effective'])]:
            response=self.client.post(url,{'note':'Review'})
            self.assertIn(reverse('reauthenticate'),response.url)
        self.case.refresh_from_db();x.refresh_from_db()
        self.assertEqual(self.case.status,'investigating');self.assertEqual(x.status,'done')

    def test_capa_creator_cannot_lend_verification_to_a_deputy(self):
        x=CM.add_capa(self.reviewer,self.case,'Creator independence','Cause','Action',self.checker,self.today+timedelta(days=2))
        CM.implement(self.checker,x.pk,'Implemented by owner, distinct from creator')
        self.grant('verify_capaaction',self.reviewer)
        x.refresh_from_db()
        self.assertFalse(CM.can_verify(self.reviewer,x));self.assertFalse(CM.can_verify(self.deputy,x))
        with self.assertRaises(ValidationError):CM.verify(self.reviewer,x.pk,True,'Creator self review denied')
        with self.assertRaises((PermissionDenied,ValidationError)):CM.verify(self.deputy,x.pk,True,'Creator proxy denied')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
