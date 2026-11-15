"""Onboarding stage routing, finite delegation and retained reviewer independence."""
from datetime import timedelta
from uuid import uuid4
from django.contrib.auth.models import User, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts import delegation as D, approval_work as AW, work_handoff as H
from accounts.models import ApprovalDelegationUse, ApprovalRouting, UserProfile
from accounts.test_support import force_login_recent
from . import crm
from .models import SiteAccess, Partner, AuditEvent
from .test_crm import seed_crm,submitted


@override_settings(EMAIL_OTP_ENABLED=False)
class CRMApprovalTests(TestCase):
    def setUp(self):
        seed_crm(self)
        self.app=submitted(self)
        self.admin=User.objects.create_superuser('crm-routing-admin','qa@example.com','Fictional-password-928!')
        self.deputy=User.objects.create_user('crm-routing-deputy')
        self.deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_dealerapplication','view_team_crm','view_crmlead','view_dealerdocument']))
        SiteAccess.objects.create(user=self.deputy,site=self.site)

    def grant(self,permission='review_dealerkyc',principal=None):
        now=timezone.now()
        return D.create(self.admin,principal or self.checker,self.deputy,permission,self.site,now,now+timedelta(days=1),'Planned onboarding review cover')

    def test_three_delegated_lanes_record_actual_actor_without_granting_roles(self):
        for permission,principal,action,credit,terms in [('review_dealerkyc',self.checker,'kyc',None,None),
                ('review_dealercredit',self.finance,'credit','75000','21'),('approve_dealerapplication',self.final,'approve',None,None)]:
            # Each lane needs a distinct actual actor; deputies cannot replace that separation.
            if action!='kyc':
                previous=self.deputy
                self.deputy=User.objects.create_user('crm-deputy-'+action)
                self.deputy.user_permissions.set(previous.user_permissions.all());SiteAccess.objects.create(user=self.deputy,site=self.site)
            grant=self.grant(permission,principal)
            crm.decide_application(self.deputy,self.app.pk,action,'Independent delegated review',credit,terms)
            self.app.refresh_from_db()
            self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=self.deputy,reference=crm.review_reference(self.app,permission)).exists())
            self.assertFalse(self.deputy.has_perm('erp.'+permission))
        self.assertEqual(self.app.status,'approved');self.assertEqual(self.app.approved_by_id,self.deputy.pk)
        self.assertEqual(self.app.partner.credit_limit,75000)

    def test_prior_delegation_principal_cannot_review_next_lane(self):
        grant=self.grant();crm.decide_application(self.deputy,self.app.pk,'kyc','Evidence checked by deputy')
        D.revoke(self.admin,grant.pk,'Review completed; retained principal remains independent')
        self.app.refresh_from_db()
        task=AW.source('dealer_credit',self.app.pk)
        self.assertIn(self.checker.pk,task['makers']);self.assertIn(self.deputy.pk,task['makers'])
        self.assertFalse(AW.eligible(self.checker,task)[0])
        with self.assertRaises(ValidationError):crm.decide_application(self.checker,self.app.pk,'credit','KYC principal cannot perform finance review','75000','21')
        crm.decide_application(self.finance,self.app.pk,'credit','Independent finance reviewer','75000','21')
        self.app.refresh_from_db();self.assertEqual(self.app.status,'credit')

    def test_credit_principal_cannot_become_final_checker(self):
        crm.decide_application(self.checker,self.app.pk,'kyc','Independent KYC')
        self.grant('review_dealercredit',self.finance)
        crm.decide_application(self.deputy,self.app.pk,'credit','Deputy credit review','75000','21')
        with self.assertRaises(ValidationError):crm.decide_application(self.finance,self.app.pk,'approve','Credit principal cannot activate')
        crm.decide_application(self.final,self.app.pk,'approve','Independent final activation')

    def test_principal_maker_is_excluded_and_failed_action_rolls_back_use(self):
        self.grant(principal=self.maker)
        self.assertFalse(crm.can_review_application(self.deputy,self.app))
        with self.assertRaises((PermissionDenied,ValidationError)):crm.decide_application(self.deputy,self.app.pk,'kyc','Maker proxy denied')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        D.revoke(self.admin,D._grant(self.deputy,'review_dealerkyc',self.site).pk,'Replace maker grant')
        self.grant()
        with self.assertRaises(ValidationError):crm.decide_application(self.deputy,self.app.pk,'kyc','')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.app.refresh_from_db();self.assertEqual(self.app.status,'submitted')

    def test_readonly_buttons_catalog_and_queue_do_not_consume_delegation(self):
        self.grant()
        task=AW.source('dealer_kyc',self.app.pk)
        self.assertEqual(AW.source_url(task),reverse('crm_application_detail',args=[self.app.pk]))
        self.assertTrue(AW.eligible(self.deputy,task)[0])
        self.assertTrue(crm.reviewable(type(self.app).objects.all(),self.deputy,'review_dealerkyc').filter(pk=self.app.pk).exists())
        force_login_recent(self.client,self.deputy)
        response=self.client.get(reverse('crm_application_detail',args=[self.app.pk]));self.assertTrue(response.context['can_review'])
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_expiry_revocation_and_scope_loss_remove_review_authority(self):
        grant=self.grant()
        from unittest.mock import patch
        with patch('accounts.delegation.timezone.now',return_value=grant.ends_at):
            self.assertFalse(crm.can_review_application(self.deputy,self.app))
            with self.assertRaises(PermissionDenied):crm.decide_application(self.deputy,self.app.pk,'kyc','Expired authority')
        SiteAccess.objects.filter(user=self.deputy,site=self.site).delete()
        with self.assertRaises(PermissionDenied):crm.decide_application(self.deputy,self.app.pk,'kyc','Site scope removed')
        SiteAccess.objects.create(user=self.deputy,site=self.site)
        D.revoke(self.admin,grant.pk,'Coverage ended')
        with self.assertRaises(PermissionDenied):crm.decide_application(self.deputy,self.app.pk,'kyc','Revoked authority')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_routing_is_stage_scoped_and_disable_handoff_preserves_evidence(self):
        self.grant()
        H.assign_approval(self.admin,'dealer_kyc',self.app.pk,self.checker,'Accountable KYC reviewer',uuid4())
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':self.deputy})
        before=self.app.submission_snapshot
        with self.captureOnCommitCallbacks(execute=True):
            self.checker.is_active=False;self.checker.save(update_fields=['is_active'])
        # The deputy's principal is unavailable, so delegation cannot substitute missing authority.
        route=ApprovalRouting.objects.get(kind='dealer_kyc',object_id=self.app.pk)
        self.assertEqual(route.assignee_id,self.checker.pk)
        self.app.refresh_from_db();self.assertEqual(self.app.submission_snapshot,before)
        self.assertEqual(self.app.status,'submitted')
        H.assign_approval(self.admin,'dealer_kyc',self.app.pk,self.finance,'Independent available reviewer',uuid4())
        self.assertEqual(len(H.assigned_pending(self.finance)),1)
        crm.decide_application(self.finance,self.app.pk,'kyc','Direct independent review')
        self.assertIsNone(AW.source('dealer_kyc',self.app.pk))
        self.assertEqual(H.assigned_pending(self.finance),[])
        self.assertIsNotNone(AW.source('dealer_credit',self.app.pk))

    def test_kyc_and_return_decisions_require_recent_identity_confirmation(self):
        self.client.force_login(self.checker)
        for action in ['kyc','changes','reject']:
            response=self.client.post(reverse('crm_application_action',args=[self.app.pk,action]),{'note':'Review'})
            self.assertIn(reverse('reauthenticate'),response.url)
        self.app.refresh_from_db();self.assertEqual(self.app.status,'submitted')

    def test_qualified_backup_receives_pending_routing_without_changing_application(self):
        H.assign_approval(self.admin,'dealer_kyc',self.app.pk,self.checker,'Accountable KYC reviewer',uuid4())
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':self.finance})
        before=self.app.submission_snapshot
        with self.captureOnCommitCallbacks(execute=True):
            self.checker.is_active=False;self.checker.save(update_fields=['is_active'])
        route=ApprovalRouting.objects.get(kind='dealer_kyc',object_id=self.app.pk)
        self.assertEqual(route.assignee_id,self.finance.pk)
        self.app.refresh_from_db();self.assertEqual(self.app.status,'submitted');self.assertEqual(self.app.submission_snapshot,before)
        self.assertEqual(len(H.assigned_pending(self.finance)),1)

    def test_invalid_credit_rolls_back_delegated_use_and_review(self):
        crm.decide_application(self.checker,self.app.pk,'kyc','Direct KYC reviewer')
        self.grant('review_dealercredit',self.finance)
        before=self.app.reviews.count()
        with self.assertRaises(ValidationError):crm.decide_application(self.deputy,self.app.pk,'credit','Invalid amount exceeds request','99999999','21')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.app.refresh_from_db();self.assertEqual(self.app.status,'kyc');self.assertEqual(self.app.reviews.count(),before)

    def test_principal_scope_loss_invalidates_existing_delegation(self):
        self.grant()
        self.checker.user_permissions.remove(Permission.objects.get(codename='view_team_crm'))
        self.assertFalse(crm.can_review_application(self.deputy,self.app))
        with self.assertRaises(PermissionDenied):crm.decide_application(self.deputy,self.app.pk,'kyc','Principal scope lost')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
