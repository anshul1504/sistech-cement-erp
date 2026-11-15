import uuid
from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase,override_settings
from django.core.exceptions import ValidationError,PermissionDenied
from django.contrib.auth.models import User,Permission
from django.db import IntegrityError,transaction
from django.utils import timezone
from django.urls import reverse
from accounts import delegation as D,approval_work as Work
from accounts.models import ApprovalDelegationUse
from accounts.test_support import force_login_recent
from . import crm,crm_merge as M,test_crm as fixture
from .models import CRMLead,CRMLeadMerge,CRMActivity,SiteAccess

@override_settings(EMAIL_OTP_ENABLED=False)
class MergeTests(TestCase):
    def setUp(self):
        fixture.seed_crm(self)
        self.source=fixture.new_lead(self,mobile='9876543210')
        self.target=fixture.new_lead(self,mobile='9876543211')

    def propose(self):
        _,token=M.preview(self.source,self.target)
        return M.propose(self.maker,self.source.pk,self.target.pk,'Same prospect confirmed by owner',uuid.uuid4(),token)

    def test_retained_independent_merge_and_earlier_followup(self):
        self.target.next_follow_up=self.today+timedelta(days=4);self.target.save()
        activity=crm.record_activity(self.maker,self.source.pk,dict(kind='call',outcome='Identity confirmed',note='Original conversation',next_follow_up=self.today,happened_on=self.today))
        self.source.refresh_from_db();item=self.propose()
        with self.assertRaisesMessage(ValidationError,'maker-checker'):M.decide(self.maker,item.pk,'approve','Own request')
        M.decide(self.checker,item.pk,'approve','Contact and business identity independently matched')
        self.source.refresh_from_db();self.target.refresh_from_db();activity.refresh_from_db();item.refresh_from_db()
        self.assertEqual((self.source.stage,self.source.merged_into_id),('merged',self.target.pk))
        self.assertEqual(activity.lead_id,self.source.pk)
        self.assertEqual(self.source.mobile,'9876543210');self.assertEqual(self.target.mobile,'9876543211')
        self.assertEqual(self.target.next_follow_up,self.today);self.assertIsNone(self.source.next_follow_up)
        self.assertEqual(item.reviewed_by_id,self.checker.pk)
        self.assertEqual(item.snapshot['source']['stage'],'contacted')
        with self.assertRaises(ValidationError):M.decide(self.checker,item.pk,'approve','Retry')

    def test_request_retry_cancel_and_stale_source(self):
        item=self.propose()
        self.assertEqual(M.propose(self.maker,self.source.pk,self.target.pk,item.reason,item.request_key,'stale').pk,item.pk)
        with self.assertRaises(ValidationError):M.propose(self.maker,self.source.pk,self.target.pk,'Conflicting reason',item.request_key,'stale')
        CRMLead.objects.filter(pk=self.target.pk).update(contact_name='Changed after preview')
        with self.assertRaisesMessage(ValidationError,'changed'):M.decide(self.checker,item.pk,'approve','Old preview')
        M.decide(self.checker,item.pk,'reject','Target changed')
        self.target.refresh_from_db();fresh=self.propose();M.decide(self.maker,fresh.pk,'cancel','Withdrawn')
        self.source.refresh_from_db();self.assertFalse(self.source.merged_into_id)
        self.assertEqual(CRMLeadMerge.objects.count(),2)

    def test_merged_source_services_cannot_reopen_or_create_work(self):
        item=self.propose();M.decide(self.checker,item.pk,'approve','Verified')
        for fn,args in [(crm.change_stage,('new','Reopen')),(crm.start_application,()),(crm.record_activity,(dict(kind='note',outcome='Wrong source',note=''),))]:
            with self.assertRaisesMessage(ValidationError,'read-only'):fn(self.maker,self.source.pk,*args)
        with self.assertRaises(ValidationError):crm.change_stage(self.maker,self.target.pk,'merged','Forged stage')

    def test_pending_onboarding_and_converted_source_block_merge(self):
        crm.start_application(self.maker,self.source.pk)
        with self.assertRaisesMessage(ValidationError,'onboarding'):M.preview(self.source,self.target)
        self.source.applications.all().update(status='rejected')
        self.source.stage='won'
        with self.assertRaisesMessage(ValidationError,'Converted'):M.preview(self.source,self.target)

    def test_scope_personal_view_and_foreign_site_denied(self):
        with self.assertRaises(PermissionDenied):M.propose(self.peer,self.source.pk,self.target.pk,'No team scope',uuid.uuid4(),'bad')
        other=CRMLead.objects.create(number='OTHER-MERGE',site=self.other,owner=self.outsider,business_name='Private',mobile='9876543212',created_by=self.outsider)
        with self.assertRaises((PermissionDenied,ValidationError)):M.propose(self.maker,self.source.pk,other.pk,'Foreign source',uuid.uuid4(),'bad')

    def test_delegated_review_actor_and_rollback(self):
        deputy=User.objects.create_user('merge-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_crmlead','view_team_crm']))
        SiteAccess.objects.create(user=deputy,site=self.site)
        admin=User.objects.create_superuser('merge-admin','merge@example.com','Password-816!')
        now=timezone.now();grant=D.create(admin,self.checker,deputy,'approve_crmmerge',self.site,now,now+timedelta(days=1),'CRM review cover')
        item=self.propose();task=Work.source('crm_merge',item.pk)
        self.assertTrue(Work.eligible(deputy,task)[0]);self.assertFalse(Work.eligible(self.maker,task)[0])
        self.assertEqual(Work.source_url(task),reverse('crm_lead_detail',args=[self.source.pk]))
        with patch('erp.crm_merge.crm._audit',side_effect=ValidationError('Audit unavailable')):
            with self.assertRaises(ValidationError):M.decide(deputy,item.pk,'approve','Verified')
        self.source.refresh_from_db();self.assertFalse(self.source.merged_into_id)
        self.assertFalse(ApprovalDelegationUse.objects.filter(delegation=grant).exists())
        M.decide(deputy,item.pk,'approve','Independently verified')
        item.refresh_from_db();self.assertEqual(item.reviewed_by_id,deputy.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant).exists())

    def test_alias_chain_resolves_and_self_link_constraint(self):
        item=self.propose();M.decide(self.checker,item.pk,'approve','First duplicate')
        third=fixture.new_lead(self,mobile='9876543212');self.target.refresh_from_db()
        _,token=M.preview(self.target,third)
        next_item=M.propose(self.maker,self.target.pk,third.pk,'Same canonical identity',uuid.uuid4(),token)
        M.decide(self.checker,next_item.pk,'approve','Second consolidation')
        self.source.refresh_from_db();self.assertEqual(M.canonical(self.source).pk,third.pk)
        with self.assertRaises(IntegrityError),transaction.atomic():CRMLead.objects.filter(pk=third.pk).update(stage='merged',merged_into_id=third.pk)

    def test_web_preview_request_approval_and_read_only_detail(self):
        force_login_recent(self.client,self.maker)
        url=reverse('crm_lead_merge',args=[self.source.pk])
        page=self.client.get(url,{'target':self.target.pk});self.assertContains(page,'Request independent merge review')
        _,token=M.preview(self.source,self.target)
        self.client.post(url,{'target':self.target.pk,'reason':'Same buyer verified','request_key':uuid.uuid4(),'evidence':token})
        item=CRMLeadMerge.objects.get();force_login_recent(self.client,self.checker)
        self.client.post(url,{'action':'approve','merge':item.pk,'note':'Identity independently verified'})
        self.assertContains(self.client.get(reverse('crm_lead_detail',args=[self.source.pk])),'Retained merged duplicate')

    def test_active_quote_blocks_merge_and_closed_quote_keeps_original_identity(self):
        from . import quotes
        data=dict(site=self.site,lead=self.source,partner=None,date=self.today,valid_until=self.today+timedelta(days=7),place_of_supply='23',freight_terms='for_destination',delivery_terms='',payment_terms='',notes='Fictional quote')
        quote=quotes.save(self.maker,data,[(self.bag,1,350)])
        with self.assertRaisesMessage(ValidationError,'quotations'):M.preview(self.source,self.target)
        quote.status='rejected';quote.save()
        item=self.propose();M.decide(self.checker,item.pk,'approve','Verified closed-source history')
        quote.refresh_from_db();self.assertEqual(quote.lead_id,self.source.pk)
        with self.assertRaisesMessage(ValidationError,'canonical'):quotes.save(self.maker,data,[(self.bag,1,350)])
        with self.assertRaises(ValidationError):quotes.revise(self.maker,quote.pk)

    def test_retained_activity_contributor_cannot_review_or_proxy(self):
        crm.record_activity(self.finance,self.source.pk,dict(kind='call',outcome='Identity checked',note='Prior contributor',happened_on=self.today,next_follow_up=self.today))
        self.source.refresh_from_db();item=self.propose()
        with self.assertRaisesMessage(ValidationError,'maker-checker'):M.decide(self.finance,item.pk,'approve','Own prior contribution')
        self.assertFalse(M.can_decide(self.finance,item))

    def test_missing_preview_and_duplicate_pending_request_fail(self):
        with self.assertRaisesMessage(ValidationError,'preview changed'):M.propose(self.maker,self.source.pk,self.target.pk,'Same prospect',uuid.uuid4(),'')
        item=self.propose()
        with self.assertRaisesMessage(ValidationError,'already pending'):self.propose()
        with self.assertRaises(IntegrityError),transaction.atomic():CRMLead.objects.filter(pk=self.source.pk).update(stage='merged')
        self.assertEqual(CRMLeadMerge.objects.count(),1)

    def test_personal_owner_cannot_read_other_owner_canonical_details_after_merge(self):
        CRMLead.objects.filter(pk=self.target.pk).update(owner=self.finance)
        self.target.refresh_from_db();item=self.propose();M.decide(self.checker,item.pk,'approve','Cross-owner duplicate reviewed')
        self.maker.user_permissions.remove(Permission.objects.get(codename='view_team_crm'))
        personal=User.objects.get(pk=self.maker.pk);force_login_recent(self.client,personal)
        page=self.client.get(reverse('crm_lead_detail',args=[self.source.pk]))
        self.assertContains(page,'Retained merged duplicate')
        self.assertNotContains(page,self.target.mobile)
        self.assertNotContains(page,'Open canonical')
        self.assertEqual(self.client.get(reverse('crm_lead_detail',args=[self.target.pk])).status_code,404)
