"""Consolidated freight review, company-scoped delegation and retained rejected claims."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth.models import User,Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts import delegation as D,approval_work as AW,work_handoff as H
from accounts.models import ApprovalDelegationUse,ApprovalRouting,UserProfile
from accounts.test_support import force_login_recent
from . import freight as F
from .models import FreightBill,FreightBillLine,JournalEntry,Site,SiteAccess,Dispatch,Order
from . import test_freight as freight_fixture


@override_settings(EMAIL_OTP_ENABLED=False)
class FreightApprovalTests(TestCase):
    trip=freight_fixture.FreightTests.trip

    @classmethod
    def setUpTestData(cls):freight_fixture.FreightTests.setUpTestData.__func__(cls)

    def setUp(self):
        self.deputy=User.objects.create_user('freight-deputy')
        self.deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_freightbill','access_company_finance']))
        dispatch=self.trip(self.truck,self.driver,100)
        self.bill=F.create_bill(self.admin,self.trans,'DELEGATED-FREIGHT',timezone.localdate(),{
            dispatch:dict(claimed=dispatch.freight_amount+Decimal('50'),days=1,extra=Decimal('2000'),reason='Detention claim')})
        self.dispatch=dispatch

    def grant(self,principal=None):
        now=timezone.now()
        return D.create(self.admin,principal or self.checker,self.deputy,'approve_freightbill',None,now,now+timedelta(days=1),'Consolidated freight review cover')

    def test_delegated_approval_retains_caps_actor_snapshot_and_balanced_posting(self):
        grant=self.grant();before=self.bill.payable
        F.decide(self.deputy,self.bill.pk,True,'Verified contracted freight and capped detention')
        self.bill.refresh_from_db()
        self.assertEqual(self.bill.checked_by_id,self.deputy.pk);self.assertEqual(self.bill.payable,before)
        self.assertEqual(self.bill.review_snapshot['trips'][0]['allowed_extra'],'1500.00')
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=self.deputy,reference=f'freight:{self.bill.pk}').exists())
        entry=JournalEntry.objects.get(source=f'frb:{self.bill.pk}')
        debit=sum(row.debit for row in entry.lines.all());credit=sum(row.credit for row in entry.lines.all())
        self.assertEqual(debit,credit);self.assertEqual(debit,before)
        with self.assertRaises(ValidationError):F.decide(self.deputy,self.bill.pk,True,'Duplicate review')
        self.assertEqual(ApprovalDelegationUse.objects.filter(delegation=grant).count(),1)
        self.assertFalse(self.deputy.has_perm('erp.approve_freightbill'))

    def test_rejection_releases_trip_but_retains_original_claim_evidence(self):
        self.grant();F.decide(self.deputy,self.bill.pk,False,'Wrong detention evidence')
        self.bill.refresh_from_db();snapshot=self.bill.review_snapshot
        self.assertFalse(self.bill.lines.exists())
        self.assertEqual(snapshot['trips'][0]['dispatch_id'],self.dispatch.pk)
        self.assertEqual(snapshot['trips'][0]['claimed'],str(self.dispatch.freight_amount+50))
        self.assertTrue(F.billable_trips(self.trans).filter(pk=self.dispatch.pk).exists())
        corrected=F.create_bill(self.admin,self.trans,'CORRECTED-FREIGHT',timezone.localdate(),{
            self.dispatch:dict(claimed=self.dispatch.freight_amount,days=0,extra=Decimal(0),reason='')})
        self.bill.refresh_from_db();self.assertEqual(self.bill.review_snapshot,snapshot)
        self.assertEqual(corrected.lines.get().dispatch_id,self.dispatch.pk)
        force_login_recent(self.client,self.deputy)
        self.assertContains(self.client.get(reverse('freight_bill_detail',args=[self.bill.pk])),'retained decision evidence')
        self.assertFalse(JournalEntry.objects.filter(source=f'frb:{self.bill.pk}').exists())

    def test_maker_principal_cannot_lend_authority_and_invalid_variance_rolls_back(self):
        grant=self.grant(principal=self.admin)
        self.assertFalse(F.can_decide(self.deputy,self.bill))
        with self.assertRaises((PermissionDenied,ValidationError)):F.decide(self.deputy,self.bill.pk,True,'Maker proxy denied')
        D.revoke(self.admin,grant.pk,'Replace maker grant');self.grant()
        with self.assertRaises(ValidationError):F.decide(self.deputy,self.bill.pk,True,'')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.bill.refresh_from_db();self.assertEqual(self.bill.review_snapshot,{})

    def test_posting_failure_rolls_back_use_decision_and_snapshot(self):
        self.grant()
        with patch('erp.gl.post_freight_bill',side_effect=ValidationError('Fictional closed-period failure')):
            with self.assertRaises(ValidationError):F.decide(self.deputy,self.bill.pk,True,'Verified claim')
        self.bill.refresh_from_db();self.assertEqual(self.bill.status,'submitted');self.assertEqual(self.bill.review_snapshot,{})
        self.assertFalse(ApprovalDelegationUse.objects.exists());self.assertTrue(self.bill.lines.exists())

    def test_expiry_and_company_scope_are_checked_on_each_use(self):
        grant=self.grant()
        with patch('accounts.delegation.timezone.now',return_value=grant.ends_at):
            self.assertFalse(F.can_decide(self.deputy,self.bill))
            with self.assertRaises(PermissionDenied):F.decide(self.deputy,self.bill.pk,True,'Expired grant')
        self.deputy.user_permissions.remove(Permission.objects.get(codename='access_company_finance'))
        self.deputy=User.objects.get(pk=self.deputy.pk)
        with self.assertRaises(PermissionDenied):F.decide(self.deputy,self.bill.pk,True,'Scope lost')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_direct_site_checker_cannot_read_or_review_partial_consolidated_scope(self):
        other=Site.objects.create(code='FRT-OTHER',name='Other site',kind='Depot')
        order=Order.objects.create(site=other,partner=self.dealer,kind='Sale',created_by=self.admin)
        dispatch=Dispatch.objects.create(number='FRT-OTHER-TRIP',token=1,site=other,order=order,vehicle=self.truck2,driver=self.driver2,transporter=self.trans,created_by=self.admin,status='gate_out',freight_amount=1000,freight_payable_by='company')
        FreightBillLine.objects.create(bill=self.bill,dispatch=dispatch,contracted=1000,claimed=1000)
        local=User.objects.create_user('freight-local-checker')
        local.user_permissions.set(Permission.objects.filter(codename__in=['view_freightbill','approve_freightbill']))
        SiteAccess.objects.create(user=local,site=self.site)
        self.assertFalse(F.can_decide(local,self.bill))
        with self.assertRaises(PermissionDenied):F.decide(local,self.bill.pk,True,'Partial site coverage')
        SiteAccess.objects.create(user=local,site=other)
        self.assertTrue(F.can_decide(local,self.bill))

    def test_routing_inbox_and_qualified_disable_backup_do_not_approve_source(self):
        backup=User.objects.create_superuser('freight-backup','backup@example.com','Fictional-password-973!')
        task=AW.source('freight_bill',self.bill.pk)
        self.assertEqual(AW.source_url(task),reverse('freight_bill_detail',args=[self.bill.pk]))
        self.assertFalse(AW.eligible(self.admin,task)[0]);self.assertTrue(AW.eligible(self.checker,task)[0])
        H.assign_approval(self.admin,'freight_bill',self.bill.pk,self.checker,'Accountable freight review',uuid4())
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':backup})
        with self.captureOnCommitCallbacks(execute=True):
            self.checker.is_active=False;self.checker.save(update_fields=['is_active'])
        self.assertEqual(ApprovalRouting.objects.get(kind='freight_bill',object_id=self.bill.pk).assignee_id,backup.pk)
        self.bill.refresh_from_db();self.assertEqual(self.bill.status,'submitted')
        self.assertEqual(len(H.assigned_pending(backup)),1)
        F.decide(backup,self.bill.pk,True,'Independent qualified backup review')
        self.assertIsNone(AW.source('freight_bill',self.bill.pk));self.assertEqual(H.assigned_pending(backup),[])

    def test_web_decision_needs_recent_identity_confirmation(self):
        self.client.force_login(self.checker)
        response=self.client.post(reverse('freight_bill_decide',args=[self.bill.pk,'approve']),{'note':'Review'})
        self.assertIn(reverse('reauthenticate'),response.url)
        self.bill.refresh_from_db();self.assertEqual(self.bill.status,'submitted')
