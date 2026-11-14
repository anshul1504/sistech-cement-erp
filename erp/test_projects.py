"""Unified project attribution, forecast and source-evidence regressions."""
from datetime import timedelta
from decimal import Decimal as D
from django.contrib.auth.models import User,Permission
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from . import projects,quotes as QT
from .models import (SalesProject,ProjectLink,ProjectEvent,ProjectActivity,Quotation,Order,OrderLine,Partner,CRMLead,Influencer,InfluencerActivity,
                     SiteAccess,Invoice,Receipt,JournalEntry,AuditEvent)
from .test_support import seed_business_data
from .test_schemes import invoice_fixture,credit_fixture


def seed_projects(target):
    seed_business_data(target);target.today=timezone.localdate()
    target.rep=User.objects.create_user('project-rep')
    target.rep.user_permissions.set(Permission.objects.filter(codename__in=['view_salesproject','add_salesproject','change_salesproject','add_projectactivity',
        'view_partner','view_crmlead','view_influencer','view_quotation','add_quotation','change_quotation','view_sales_order','create_sales_order','view_invoice']))
    SiteAccess.objects.create(user=target.rep,site=target.site)


def project_data(target,**changes):
    data=dict(site=target.site,owner=target.rep,name='Fictional building project',location='QA construction site, Bhopal',district='Bhopal',contact_name='QA Contact',mobile='+91 98765 43210',
        prospect_name='',partner=target.customer,lead='',referral='',estimated_value='100000',cement_bags=1000,tmt_mt='25',expected_close=target.today+timedelta(days=30),next_action_on=target.today,notes='Fictional planning record')
    data.update(changes);return data


def make_project(target,**changes):return projects.save(target.rep,project_data(target,**changes))


def make_quote(target,partner=None,lead=None):
    data=dict(site=target.site,partner=partner or (None if lead else target.customer),lead=lead,date=target.today,valid_until=target.today+timedelta(days=10),
        place_of_supply='23',freight_terms='for_destination',delivery_terms='',payment_terms='',notes='Fictional project offer')
    return QT.save(target.maker,data,[(target.bag,D(10),D(100))])


def sent_quote(target):
    q=make_quote(target);QT.submit(target.maker,q.pk);QT.mark_sent(target.maker,q.pk);q.refresh_from_db();return q


def link_quote(target,p,q):return projects.link(target.rep,p.pk,dict(quotation=q,order=None,note='Offer matched to project'))


def link_order(target,p,order):return projects.link(target.rep,p.pk,dict(order=order,quotation=None,note='Order matched to project'))


@override_settings(EMAIL_OTP_ENABLED=False)
class ProjectTests(TestCase):
    def setUp(self):seed_projects(self);self.client.force_login(self.rep)

    def test_profile_normalization_duplicate_identity_and_owner_site(self):
        p=make_project(self)
        self.assertEqual(p.mobile,'9876543210')
        with self.assertRaises(ValidationError):make_project(self,name=' fictional   BUILDING project ',location=' qa construction site,   BHOPAL ')
        self.assertEqual(SalesProject.objects.count(),1)
        with self.assertRaises(PermissionDenied):projects.save(self.outsider,project_data(self,owner=self.outsider))
        with self.assertRaises(PermissionDenied):projects.save(self.maker,project_data(self,owner=self.outsider,name='Other'))

    def test_own_team_and_foreign_site_reads_mutations_exports(self):
        p=make_project(self)
        peer=User.objects.create_user('project-peer');peer.user_permissions.set(self.rep.user_permissions.all());SiteAccess.objects.create(user=peer,site=self.site)
        for actor in [peer,self.outsider]:
            self.client.force_login(actor)
            self.assertEqual(self.client.get(reverse('project_detail',args=[p.pk])).status_code,404)
            self.assertNotContains(self.client.get(reverse('projects')),p.number)
            with self.assertRaises(PermissionDenied):projects.activity(actor,p.pk,dict(kind='call',on=self.today,note='Foreign',next_action_on=self.today))
        self.client.force_login(self.checker)
        self.assertEqual(self.client.get(reverse('project_detail',args=[p.pk])).status_code,200)

    def test_source_party_requires_onboarded_crm_customer(self):
        lead=CRMLead.objects.create(number='PJ-LEAD',site=self.site,owner=self.rep,business_name='QA Prospect',mobile='9876543210',created_by=self.rep)
        with self.assertRaises(ValidationError):make_project(self,lead=lead)
        p=make_project(self,partner='',lead=lead,prospect_name='QA Prospect')
        self.assertIsNone(p.partner_id)
        self.assertEqual(Partner.objects.count(),2)

    def test_legacy_referral_import_is_once_and_retains_original_source(self):
        person=Influencer.objects.create(code='PJ-INF',kind='Contractor',name='QA Contractor',mobile='8765432109',district='Bhopal')
        referral=InfluencerActivity.objects.create(influencer=person,kind='referral',title='QA apartment',location='Bhopal',dealer=self.customer,
            expected_bags=2500,expected_tmt_mt=10,status='open',created_by=self.rep)
        p=make_project(self,referral=referral)
        self.assertEqual(p.source_snapshot['expected_bags'],2500)
        with self.assertRaises(ValidationError):make_project(self,referral=referral,name='Second import')
        referral.status='won';referral.save(update_fields=['status'])
        p.refresh_from_db();self.assertEqual(p.source_snapshot['status'],'open');self.assertEqual(p.stage,'new')

    def test_activity_and_sampling_require_evidence_and_valid_dates(self):
        p=make_project(self)
        with self.assertRaises(ValidationError):projects.change_stage(self.rep,p.pk,'sampling','No sample',self.today)
        for on,next_on in [(self.today+timedelta(days=1),self.today),(self.today,self.today-timedelta(days=1))]:
            with self.assertRaises(ValidationError):projects.activity(self.rep,p.pk,dict(kind='sample',on=on,note='QA sample',next_action_on=next_on))
        projects.activity(self.rep,p.pk,dict(kind='sample',on=self.today,note='Technical sample reviewed',next_action_on=self.today+timedelta(days=1)))
        p=projects.change_stage(self.rep,p.pk,'sampling','Sample evidence recorded',self.today+timedelta(days=1))
        self.assertEqual(p.stage,'sampling')
        self.assertEqual(ProjectActivity.objects.count(),1)

    def test_quoted_negotiation_and_won_are_gated_by_live_evidence(self):
        p=make_project(self);q=make_quote(self);link_quote(self,p,q)
        with self.assertRaises(ValidationError):projects.change_stage(self.rep,p.pk,'quoted','Draft only',self.today)
        QT.submit(self.maker,q.pk);QT.mark_sent(self.maker,q.pk)
        projects.change_stage(self.rep,p.pk,'quoted','Offer sent',self.today)
        projects.change_stage(self.rep,p.pk,'negotiation','Discussing offer',self.today)
        with self.assertRaises(ValidationError):projects.change_stage(self.rep,p.pk,'won','No order yet')
        QT.outcome(self.maker,q.pk,True,'Accepted');order=QT.convert(self.maker,q.pk)
        link_order(self,p,order)
        with self.assertRaises(ValidationError):projects.change_stage(self.rep,p.pk,'won','Draft order only')
        order.status='Approved';order.approved_by=self.checker;order.save(update_fields=['status','approved_by'])
        p=projects.change_stage(self.rep,p.pk,'won','Independently approved order confirmed')
        self.assertEqual(p.stage,'won');self.assertIsNone(p.next_action_on)
        event=p.events.first()
        self.assertTrue(any(l['current']['status']=='Approved' for l in event.snapshot['links']))

    def test_expired_or_changed_party_quotes_do_not_qualify(self):
        p=make_project(self);q=sent_quote(self);link_quote(self,p,q)
        Quotation.objects.filter(pk=q.pk).update(valid_until=self.today-timedelta(days=1))
        with self.assertRaises(ValidationError):projects.change_stage(self.rep,p.pk,'quoted','Expired',self.today)
        Quotation.objects.filter(pk=q.pk).update(valid_until=self.today+timedelta(days=1),partner=self.supplier)
        with self.assertRaises(ValidationError):projects.change_stage(self.rep,p.pk,'quoted','Wrong party',self.today)

    def test_sources_cannot_be_double_attributed_or_deleted_from_history(self):
        p=make_project(self);other=make_project(self,name='Other project');q=sent_quote(self)
        link=link_quote(self,p,q)
        self.assertEqual(link_quote(self,p,q).pk,link.pk)
        with self.assertRaises(ValidationError):link_quote(self,other,q)
        projects.unlink(self.rep,p.pk,link.pk,'Incorrect original attribution')
        link.refresh_from_db();self.assertFalse(link.active);self.assertIsNotNone(link.unlinked_at)
        moved=link_quote(self,other,q);self.assertNotEqual(moved.pk,link.pk)
        self.assertEqual(ProjectLink.objects.count(),2)

    def test_revision_family_and_converted_order_keep_project_attribution(self):
        p=make_project(self);other=make_project(self,name='Other project');q=sent_quote(self);link_quote(self,p,q)
        revised=QT.revise(self.maker,q.pk)
        with self.assertRaises(ValidationError):link_quote(self,other,revised)
        QT.submit(self.maker,revised.pk);QT.mark_sent(self.maker,revised.pk);QT.outcome(self.maker,revised.pk,True,'Accepted')
        link_quote(self,p,revised)
        order=QT.convert(self.maker,revised.pk)
        with self.assertRaises(ValidationError):link_order(self,other,order)
        link_order(self,p,order)

    def test_unlink_last_quote_requires_stage_review_and_retains_context(self):
        p=make_project(self);q=sent_quote(self);link=link_quote(self,p,q)
        projects.change_stage(self.rep,p.pk,'quoted','Offer sent',self.today)
        with self.assertRaises(ValidationError):projects.unlink(self.rep,p.pk,link.pk,'Remove only evidence')
        projects.change_stage(self.rep,p.pk,'qualified','Reassess offer',self.today)
        projects.unlink(self.rep,p.pk,link.pk,'Offer withdrawn from project attribution')
        self.assertEqual(p.events.filter(action='Source unlinked').count(),1)

    def test_closed_project_requires_manager_reopening_without_source_rewrite(self):
        p=make_project(self);projects.change_stage(self.rep,p.pk,'lost','Budget not approved')
        with self.assertRaises(ValidationError):projects.activity(self.rep,p.pk,dict(kind='call',on=self.today,note='Closed',next_action_on=self.today))
        with self.assertRaises(PermissionDenied):projects.reopen(self.rep,p.pk,'Reopen',self.today)
        reopened=projects.reopen(self.checker,p.pk,'Budget reopened',self.today)
        self.assertEqual(reopened.stage,'qualified')
        closed=ProjectEvent.objects.filter(project=p,action='Stage changed').first()
        self.assertEqual(closed.snapshot['stage'],'lost');self.assertEqual(closed.note,'Budget not approved')

    def test_prospect_onboarding_binding_preserves_lead_quotation(self):
        lead=CRMLead.objects.create(number='PJ-LEAD',site=self.site,owner=self.rep,business_name='QA Prospect',mobile='9876543210',created_by=self.rep)
        p=make_project(self,partner='',lead=lead,prospect_name='QA Prospect')
        q=make_quote(self,lead=lead);link_quote(self,p,q)
        lead.partner=self.customer;lead.stage='won';lead.save()
        updated=projects.save(self.rep,project_data(self,lead=lead,prospect_name='QA Prospect'),p.pk)
        self.assertEqual(updated.partner_id,self.customer.pk)
        self.assertEqual(updated.links.filter(active=True).count(),1)

    def test_wrong_site_and_other_buyer_sources_rejected(self):
        p=make_project(self)
        order=Order.objects.create(site=self.other,partner=self.customer,kind='Sale',created_by=self.maker)
        with self.assertRaises(ValidationError):link_order(self,p,order)
        wrong=make_quote(self,partner=Partner.objects.create(code='PJ-OTHER',name='Other buyer',kind='Dealer'))
        with self.assertRaises(ValidationError):link_quote(self,p,wrong)
        self.assertEqual(p.links.count(),0)

    def test_live_invoice_totals_are_distinct_from_estimate_and_include_credits(self):
        p=make_project(self)
        inv=invoice_fixture(self);credit_fixture(self,inv,100,inv.lines.first())
        link_order(self,p,inv.order)
        values=projects.invoice_summary(self.rep,p)
        self.assertEqual((values['taxable'],values['credits'],values['net']),(D(1000),D(100),D(900)))
        self.assertEqual(p.estimated_value,D(100000))
        self.assertEqual(projects.pipeline(self.rep,SalesProject.objects.filter(pk=p.pk))[0]['weighted'],D(10000))
        self.rep.user_permissions.remove(Permission.objects.get(codename='view_invoice'))
        self.assertIsNone(projects.invoice_summary(User.objects.get(pk=self.rep.pk),p))

    def test_profile_identity_cannot_change_under_active_commercial_links(self):
        p=make_project(self);link_quote(self,p,make_quote(self))
        with self.assertRaises(ValidationError):projects.save(self.rep,project_data(self,partner='',prospect_name='Different buyer'),p.pk)
        p.refresh_from_db();self.assertEqual(p.partner_id,self.customer.pk)

    def test_forecast_values_dates_and_mobile_are_validated(self):
        for values in [dict(estimated_value='NaN'),dict(tmt_mt='Infinity'),dict(estimated_value='-1'),dict(mobile='bad'),dict(next_action_on=None),dict(expected_close=self.today-timedelta(days=1))]:
            with self.subTest(values=values),self.assertRaises(ValidationError):make_project(self,**values)
        self.assertFalse(SalesProject.objects.exists())

    def test_actual_quote_form_links_project_atomically(self):
        p=make_project(self)
        page=self.client.get(reverse('quotation_new')+f'?project_id={p.pk}')
        self.assertEqual(page.status_code,200);self.assertEqual(page.context['v']['partner'],str(self.customer.pk))
        payload=dict(project_id=p.pk,site=self.site.pk,partner=self.customer.pk,date=self.today,valid_until=self.today+timedelta(days=5),place_of_supply='23',freight_terms='for_destination',product_0=self.bag.pk,qty_0='10',rate_0='100')
        response=self.client.post(reverse('quotation_new'),payload)
        self.assertEqual(response.status_code,302);self.assertEqual(p.links.count(),1)
        count=Quotation.objects.count()
        payload['partner']=Partner.objects.create(code='PJ-HTTP-OTHER',name='Other HTTP buyer',kind='Dealer').pk
        self.client.post(reverse('quotation_new'),payload)
        self.assertEqual(Quotation.objects.count(),count)

    def test_read_only_actions_exports_and_project_pages(self):
        p=make_project(self)
        viewer=User.objects.create_user('project-viewer');viewer.user_permissions.set(Permission.objects.filter(codename__in=['view_salesproject','view_team_projects']))
        SiteAccess.objects.create(user=viewer,site=self.site);self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('project_detail',args=[p.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('project_action',args=[p.pk,'activity']),{}).status_code,403)
        self.assertEqual(self.client.get(reverse('projects')+'?export=csv').status_code,403)
        self.assertEqual(self.client.get(reverse('quotation_new')+f'?project_id={p.pk}').status_code,403)
        self.client.force_login(self.maker)
        for name,args in [('projects',[]),('project_new',[]),('project_edit',[p.pk]),('project_detail',[p.pk])]:
            self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200)
        SalesProject.objects.filter(pk=p.pk).update(name='=SUM(A1:A2)')
        self.assertContains(self.client.get(reverse('projects')+'?export=csv'),"'=SUM(A1:A2)")
        self.assertTrue(AuditEvent.objects.filter(action='Project pipeline exported').exists())

    def test_source_changes_warn_without_overwriting_recorded_outcome(self):
        p=make_project(self);order=Order.objects.create(site=self.site,partner=self.customer,kind='Sale',created_by=self.maker,status='Approved',approved_by=self.checker)
        link_order(self,p,order);projects.change_stage(self.rep,p.pk,'won','Order approved')
        order.status='Cancelled';order.save(update_fields=['status'])
        response=self.client.get(reverse('project_detail',args=[p.pk]))
        self.assertContains(response,'no longer satisfy win evidence')
        p.refresh_from_db();self.assertEqual(p.stage,'won')
        self.assertTrue(any(l['current']['status']=='Approved' for l in p.events.filter(action='Stage changed').first().snapshot['links']))

    def test_pipeline_writes_do_not_create_financial_or_stock_transactions(self):
        p=make_project(self)
        before=(Invoice.objects.count(),Receipt.objects.count(),JournalEntry.objects.count())
        projects.activity(self.rep,p.pk,dict(kind='call',on=self.today,note='Budget discussed',next_action_on=self.today))
        projects.change_stage(self.rep,p.pk,'qualified','Confirmed scope',self.today)
        self.assertEqual((Invoice.objects.count(),Receipt.objects.count(),JournalEntry.objects.count()),before)

    def test_converted_order_can_be_attached_from_project_without_auto_winning(self):
        p=make_project(self);q=sent_quote(self);link_quote(self,p,q)
        QT.outcome(self.maker,q.pk,True,'Accepted');order=QT.convert(self.maker,q.pk)
        page=self.client.get(reverse('project_detail',args=[p.pk]))
        self.assertContains(page,'Link converted sales order')
        payload={'source-order':order.pk,'source-note':'Converted offer mapped to construction site'}
        for _ in range(2):self.assertEqual(self.client.post(reverse('project_action',args=[p.pk,'link']),payload).status_code,302)
        self.assertEqual(p.links.filter(active=True).count(),2)
        p.refresh_from_db();self.assertEqual(p.stage,'new');self.assertFalse(Invoice.objects.exists())

    def test_quote_context_is_hidden_from_other_project_owners(self):
        p=make_project(self);q=make_quote(self);link_quote(self,p,q)
        self.assertContains(self.client.get(reverse('quotation_detail',args=[q.pk])),'Project '+p.number)
        peer=User.objects.create_user('project-quote-peer');peer.user_permissions.set(self.rep.user_permissions.all());SiteAccess.objects.create(user=peer,site=self.site)
        self.client.force_login(peer)
        self.assertNotContains(self.client.get(reverse('quotation_detail',args=[q.pk])),'Project '+p.number)

    def test_imported_referral_and_customer_history_cannot_be_deleted(self):
        person=Influencer.objects.create(code='PJ-INF',kind='Contractor',name='QA Contractor',mobile='8765432109',district='Bhopal')
        referral=InfluencerActivity.objects.create(influencer=person,kind='referral',title='QA apartment',dealer=self.customer,status='open',created_by=self.maker)
        p=make_project(self,referral=referral)
        self.client.force_login(self.maker)
        self.assertEqual(self.client.post(reverse('influencer_activity_delete',args=[person.pk,referral.pk])).status_code,302)
        self.assertTrue(InfluencerActivity.objects.filter(pk=referral.pk).exists())
        admin=User.objects.create_superuser('project-admin','project-admin@example.com','Verification-Password-816!')
        self.client.force_login(admin)
        self.client.post(reverse('partner_delete',args=[self.customer.pk]))
        self.assertTrue(Partner.objects.filter(pk=self.customer.pk).exists())

    def test_project_due_queue_and_closed_offer_creation(self):
        from .insights import my_queue
        p=make_project(self)
        self.assertTrue(any(i['label']=='Project follow-ups due' for i in my_queue(self.rep)))
        projects.change_stage(self.rep,p.pk,'lost','No budget')
        self.assertFalse(any(i['label']=='Project follow-ups due' for i in my_queue(self.rep)))
        response=self.client.get(reverse('quotation_new')+f'?project_id={p.pk}')
        self.assertEqual(response.status_code,302)
