"""Fictional field coverage and dated-ownership workflow regressions."""
from datetime import timedelta
from decimal import Decimal as D
from django.contrib.auth.models import User,Permission
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from . import beats
from .models import (SalesTerritory,DealerTerritoryAssignment,BeatPlan,BeatStop,BeatReview,SiteAccess,Partner,
                     CollectionFollowUp,Receipt,JournalEntry,AuditEvent)
from .test_support import seed_business_data


def seed_beats(target):
    seed_business_data(target)
    target.today=timezone.localdate()
    target.rep=User.objects.create_user('field-rep')
    target.rep.user_permissions.set(Permission.objects.filter(codename__in=['view_beatplan','add_beatplan','change_beatplan','submit_beatplan','record_beatstop',
        'view_salesterritory','view_partner','view_crmlead','view_sales_order','view_complaint']))
    SiteAccess.objects.create(user=target.rep,site=target.site)
    target.root=beats.create_territory(target.maker,dict(site=target.site,code='MP',name='Madhya Pradesh',kind='state',parent='',state='23'))
    target.district=beats.create_territory(target.maker,dict(site=target.site,code='BPL',name='Bhopal',kind='district',parent=target.root,state=''))
    target.area=beats.create_territory(target.maker,dict(site=target.site,code='CENTRAL',name='Central beat',kind='area',parent=target.district,state=''))
    target.assignment=beats.assign(target.maker,dict(territory=target.area,partner=target.customer,owner=target.rep,starts_on=target.today,ends_on=None,note='QA ownership'))


def plan_draft(target,on=None):
    plan=beats.save_plan(target.rep,dict(site=target.site,owner=target.rep,on=on or target.today,title='Fictional dealer route'))
    stop=beats.add_stop(target.rep,plan.pk,dict(assignment=target.assignment,sequence=1,purpose='visit',instructions='QA visit'))
    return plan,stop


def approved_plan(target,on=None):
    plan,stop=plan_draft(target,on)
    beats.submit_plan(target.rep,plan.pk,'Assigned dealers and route checked')
    plan=beats.decide_plan(target.checker,plan.pk,True,'Independent route review completed')
    return plan,stop


def outcome_data(target,**changes):
    values=dict(visited_on=target.today,outcome='visited',note='Fictional visit completed',next_action_on=None,promised_amount=None,promised_on=None,lead='',order='',complaint='')
    values.update(changes);return values


@override_settings(EMAIL_OTP_ENABLED=False)
class BeatTests(TestCase):
    def setUp(self):
        seed_beats(self);self.client.force_login(self.rep)

    def test_hierarchy_is_site_bound_and_inherits_state(self):
        self.assertEqual(self.area.state,'23')
        self.assertEqual([n.kind for n in beats.path(self.area)],['state','district','area'])
        for data in [dict(site=self.site,code='BAD',name='Bad',kind='area',parent=self.root,state=''),
                     dict(site=self.other,code='BAD',name='Bad',kind='district',parent=self.root,state='')]:
            with self.assertRaises(ValidationError):beats.create_territory(self.maker,data)
        with self.assertRaises(PermissionDenied):beats.create_territory(self.rep,dict(site=self.site))
        with self.assertRaises(ValidationError):beats.create_territory(self.maker,dict(site=self.site,code='MP2',name='Other MP',kind='state',parent='',state='23'))

    def test_assignment_overlap_history_and_shared_master_unchanged(self):
        before=(self.customer.district,self.customer.territory)
        data=dict(territory=self.area,partner=self.customer,owner=self.rep,starts_on=self.today,ends_on=None,note='Other ownership')
        with self.assertRaises(ValidationError):beats.assign(self.maker,data)
        beats.end_assignment(self.maker,self.assignment.pk,self.today,'Handover after today')
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.ends_on,self.today)
        fresh=beats.assign(self.maker,dict(data,starts_on=self.today+timedelta(days=1)))
        self.assertNotEqual(fresh.pk,self.assignment.pk)
        with self.assertRaises(ValidationError):beats.end_assignment(self.maker,self.assignment.pk,self.today+timedelta(days=3),'Rewrite')
        self.customer.refresh_from_db();self.assertEqual((self.customer.district,self.customer.territory),before)

    def test_owner_must_be_active_authorized_and_site_assigned(self):
        partner=Partner.objects.create(code='FIELD-D2',name='Other dealer',kind='Dealer')
        data=dict(territory=self.area,partner=partner,owner=self.outsider,starts_on=self.today,ends_on=None,note='Foreign owner')
        with self.assertRaises(PermissionDenied):beats.assign(self.maker,data)
        self.rep.is_active=False;self.rep.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):beats.assign(self.maker,dict(data,owner=self.rep))

    def test_own_team_and_site_read_write_boundaries(self):
        plan,stop=plan_draft(self)
        for actor in [self.outsider]:
            with self.assertRaises(PermissionDenied):beats.submit_plan(actor,plan.pk,'Foreign')
            with self.assertRaises(PermissionDenied):beats.cancel_plan(actor,plan.pk,'Foreign')
        peer=User.objects.create_user('field-peer')
        peer.user_permissions.set(self.rep.user_permissions.all());SiteAccess.objects.create(user=peer,site=self.site)
        self.client.force_login(peer)
        self.assertEqual(self.client.get(reverse('beat_detail',args=[plan.pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('territories')),self.customer.name)
        self.assertEqual(beats.coverage(peer,self.today,self.today),[])
        self.client.force_login(self.checker)
        self.assertEqual(self.client.get(reverse('beat_detail',args=[plan.pk])).status_code,200)

    def test_only_one_live_route_per_site_owner_day(self):
        plan,stop=plan_draft(self)
        with self.assertRaises(ValidationError):plan_draft(self)
        beats.cancel_plan(self.rep,plan.pk,'Replace unused draft')
        replacement,_=plan_draft(self)
        self.assertNotEqual(plan.pk,replacement.pk)
        self.assertEqual(BeatPlan.objects.count(),2)

    def test_own_route_cannot_be_approved_or_edited_after_submit(self):
        plan,stop=plan_draft(self)
        beats.submit_plan(self.rep,plan.pk,'Ready')
        with self.assertRaises(PermissionDenied):beats.decide_plan(self.rep,plan.pk,True,'No approval permission')
        # A manager who contributes cannot become the checker.
        beats.decide_plan(self.checker,plan.pk,False,'Add detail')
        beats.save_plan(self.maker,dict(site=self.site,owner=self.rep,on=self.today,title='Manager edited'),plan.pk)
        beats.submit_plan(self.rep,plan.pk,'Ready again')
        with self.assertRaises(ValidationError):beats.decide_plan(self.maker,plan.pk,True,'Own edit')
        with self.assertRaises(ValidationError):beats.add_stop(self.rep,plan.pk,dict(assignment=self.assignment,sequence=2,purpose='visit',instructions=''))
        with self.assertRaises(ValidationError):beats.remove_stop(self.rep,plan.pk,stop.pk)

    def test_reviewer_checks_frozen_customer_and_assignment_evidence(self):
        plan,stop=plan_draft(self);beats.submit_plan(self.rep,plan.pk,'First evidence')
        self.customer.address='Changed fictional address';self.customer.save(update_fields=['address'])
        with self.assertRaisesMessage(ValidationError,'changed after submission'):beats.decide_plan(self.checker,plan.pk,True,'Stale')
        beats.decide_plan(self.checker,plan.pk,False,'Refresh changed address')
        beats.submit_plan(self.rep,plan.pk,'New evidence')
        beats.decide_plan(self.checker,plan.pk,True,'Refreshed')
        reviews=BeatReview.objects.filter(plan=plan,action='Submitted').order_by('revision')
        self.assertEqual(reviews.count(),2)
        self.assertNotEqual(reviews[0].snapshot['stops'][0]['customer']['address'],reviews[1].snapshot['stops'][0]['customer']['address'])

    def test_completed_and_missed_outcomes_finish_plan_and_are_immutable(self):
        partner=Partner.objects.create(code='FIELD-D2',name='Other dealer',kind='Dealer')
        assignment=beats.assign(self.maker,dict(territory=self.area,partner=partner,owner=self.rep,starts_on=self.today,ends_on=None,note='QA second'))
        plan,stop=plan_draft(self)
        second=beats.add_stop(self.rep,plan.pk,dict(assignment=assignment,sequence=2,purpose='visit',instructions=''))
        beats.submit_plan(self.rep,plan.pk,'Ready');beats.decide_plan(self.checker,plan.pk,True,'Approved')
        beats.record_stop(self.rep,stop.pk,outcome_data(self))
        with self.assertRaises(ValidationError):beats.record_stop(self.rep,stop.pk,outcome_data(self))
        with self.assertRaises(ValidationError):beats.cancel_plan(self.rep,plan.pk,'Discard visit')
        beats.record_stop(self.rep,second.pk,dict(note='Dealer unavailable'),missed=True)
        plan.refresh_from_db();self.assertEqual(plan.status,'finished')
        row=beats.coverage(self.rep,self.today,self.today)[0]
        self.assertEqual((row['assigned_count'],row['visited_count'],row['completed'],row['missed']),(2,1,1,1))
        self.assertEqual([p.pk for p in row['gaps']],[partner.pk])

    def test_future_date_and_missing_outcome_evidence_are_rejected(self):
        plan,stop=approved_plan(self,self.today+timedelta(days=1))
        with self.assertRaises(ValidationError):beats.record_stop(self.rep,stop.pk,outcome_data(self))
        with self.assertRaises(ValidationError):beats.record_stop(self.rep,stop.pk,dict(note='Not yet'),missed=True)
        self.assertEqual(BeatStop.objects.get(pk=stop.pk).status,'planned')

    def test_recorded_visit_dates_and_promises_are_validated(self):
        plan,stop=approved_plan(self)
        for changes in [dict(visited_on=self.today+timedelta(days=1)),dict(note=''),dict(visited_on=self.today-timedelta(days=1)),
                        dict(outcome='promise',promised_amount='NaN',promised_on=self.today),dict(outcome='promise',promised_amount='10'),
                        dict(promised_amount='10',promised_on=self.today),dict(outcome='complaint')]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):beats.record_stop(self.rep,stop.pk,outcome_data(self,**changes))
        self.assertFalse(BeatStop.objects.filter(status='completed').exists())

    def test_finance_accepts_original_promise_once_without_receipt_or_journal(self):
        plan,stop=approved_plan(self)
        beats.record_stop(self.rep,stop.pk,outcome_data(self,outcome='promise',promised_amount='500.25',promised_on=self.today+timedelta(days=3),next_action_on=self.today))
        self.assertFalse(CollectionFollowUp.objects.exists())
        before=(Receipt.objects.count(),JournalEntry.objects.count())
        record=beats.publish_promise(self.checker,stop.pk,'Promise independently confirmed')
        self.assertEqual((record.partner_id,record.owner_id,record.promised_amount,record.on),(self.customer.pk,self.rep.pk,D('500.25'),self.today))
        self.assertEqual(beats.publish_promise(self.checker,stop.pk,'Retry').pk,record.pk)
        self.assertEqual(CollectionFollowUp.objects.count(),1)
        self.assertEqual((Receipt.objects.count(),JournalEntry.objects.count()),before)

    def test_finance_promise_rejects_self_report_and_unassigned_site(self):
        plan,stop=approved_plan(self)
        beats.record_stop(self.maker,stop.pk,outcome_data(self,outcome='promise',promised_amount='10',promised_on=self.today))
        with self.assertRaises(ValidationError):beats.publish_promise(self.maker,stop.pk,'Self report')
        with self.assertRaises(PermissionDenied):beats.publish_promise(self.outsider,stop.pk,'Foreign')
        with self.assertRaises(PermissionDenied):beats.publish_promise(self.rep,stop.pk,'Missing finance rights')
        self.assertFalse(CollectionFollowUp.objects.exists())

    def test_followup_close_preserves_original_visit(self):
        plan,stop=approved_plan(self)
        beats.record_stop(self.rep,stop.pk,outcome_data(self,next_action_on=self.today))
        beats.close_followup(self.rep,stop.pk,'Called dealer again')
        stop.refresh_from_db();self.assertEqual(stop.note,'Fictional visit completed');self.assertIsNotNone(stop.followed_at)
        with self.assertRaises(ValidationError):beats.close_followup(self.rep,stop.pk,'Second close')

    def test_archiving_branch_blocks_new_plans_not_existing_history(self):
        plan,stop=plan_draft(self)
        beats.toggle_territory(self.maker,self.root.pk,'Region no longer served')
        with self.assertRaises(ValidationError):beats.submit_plan(self.rep,plan.pk,'Archived')
        self.assertEqual(DealerTerritoryAssignment.objects.count(),1)
        self.assertEqual(SalesTerritory.objects.count(),3)

    def test_assignment_handover_invalidates_future_submitted_plan(self):
        plan,stop=plan_draft(self,self.today+timedelta(days=1))
        beats.submit_plan(self.rep,plan.pk,'Ready tomorrow')
        beats.end_assignment(self.maker,self.assignment.pk,self.today,'Handover')
        with self.assertRaises(ValidationError):beats.decide_plan(self.checker,plan.pk,True,'Assignment expired')
        self.assertEqual(BeatPlan.objects.get(pk=plan.pk).status,'submitted')

    def test_readonly_user_cannot_post_or_export(self):
        plan,stop=plan_draft(self)
        viewer=User.objects.create_user('field-viewer')
        viewer.user_permissions.set(Permission.objects.filter(codename__in=['view_beatplan','view_team_beats','view_salesterritory']))
        SiteAccess.objects.create(user=viewer,site=self.site);self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('beat_detail',args=[plan.pk])).status_code,200)
        self.assertEqual(self.client.post(reverse('beat_action',args=[plan.pk,'stop']),{}).status_code,403)
        self.assertEqual(self.client.get(reverse('beat_report')+'?export=csv').status_code,403)
        self.assertEqual(self.client.get(reverse('territory_new',args=['territory'])).status_code,403)

    def test_scoped_report_csv_and_pages_render(self):
        plan,stop=approved_plan(self)
        for name,args in [('beat_plans',[]),('beat_new',[]),('territories',[]),('beat_report',[]),('beat_detail',[plan.pk]),('beat_outcome',[stop.pk])]:
            with self.subTest(name=name):self.assertEqual(self.client.get(reverse(name,args=args)).status_code,200)
        self.client.force_login(self.maker)
        self.assertEqual(self.client.get(reverse('territory_new',args=['assignment'])).status_code,200)
        self.assertEqual(self.client.get(reverse('territory_new',args=['territory'])).status_code,200)
        SalesTerritory.objects.filter(pk=self.area.pk).update(name='=SUM(A1:A2)')
        response=self.client.get(reverse('beat_report')+'?export=csv')
        self.assertContains(response,"'=SUM(A1:A2)")
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertTrue(AuditEvent.objects.filter(action='Beat coverage exported').exists())

    def test_invalid_dates_empty_routes_and_duplicate_stops(self):
        with self.assertRaises(ValidationError):beats.coverage(self.rep,self.today,self.today-timedelta(days=1))
        with self.assertRaises(ValidationError):beats.coverage(self.rep,self.today,self.today+timedelta(days=400))
        plan=beats.save_plan(self.rep,dict(site=self.site,owner=self.rep,on=self.today,title='Empty route'))
        with self.assertRaises(ValidationError):beats.submit_plan(self.rep,plan.pk,'Empty')
        stop=beats.add_stop(self.rep,plan.pk,dict(assignment=self.assignment,sequence=1,purpose='visit',instructions=''))
        with self.assertRaises(ValidationError):beats.add_stop(self.rep,plan.pk,dict(assignment=self.assignment,sequence=2,purpose='visit',instructions=''))
        self.assertEqual(plan.stops.count(),1)

    def test_actual_form_posts_create_plan_and_record_outcome(self):
        response=self.client.post(reverse('beat_new'),dict(site=self.site.pk,owner=self.rep.pk,on=self.today,title='HTTP field route'))
        self.assertEqual(response.status_code,302)
        plan=BeatPlan.objects.get()
        self.assertEqual(self.client.post(reverse('beat_action',args=[plan.pk,'stop']),dict(assignment=self.assignment.pk,sequence=1,purpose='visit')).status_code,302)
        beats.submit_plan(self.rep,plan.pk,'HTTP ready');beats.decide_plan(self.checker,plan.pk,True,'Reviewed')
        stop=plan.stops.get()
        response=self.client.post(reverse('beat_outcome',args=[stop.pk]),{k:v for k,v in outcome_data(self).items() if v is not None})
        self.assertEqual(response.status_code,302)
        self.assertEqual(BeatStop.objects.get(pk=stop.pk).status,'completed')

    def test_owner_with_approval_permission_still_cannot_self_approve(self):
        self.rep.user_permissions.add(Permission.objects.get(codename='approve_beatplan'))
        actor=User.objects.get(pk=self.rep.pk)
        plan,stop=plan_draft(self);beats.submit_plan(self.rep,plan.pk,'Ready')
        with self.assertRaisesMessage(ValidationError,'route owner'):beats.decide_plan(actor,plan.pk,True,'Self approval')

    def test_late_reported_promise_keeps_original_visit_and_payment_dates(self):
        from unittest.mock import patch
        plan,stop=approved_plan(self)
        with patch('erp.beats.timezone.localdate',return_value=self.today+timedelta(days=3)):
            beats.record_stop(self.rep,stop.pk,outcome_data(self,outcome='promise',promised_amount='20',promised_on=self.today+timedelta(days=1)))
            row=beats.publish_promise(self.checker,stop.pk,'Late field report confirmed')
        self.assertEqual((row.on,row.promised_date),(self.today,self.today+timedelta(days=1)))
        self.assertFalse(Receipt.objects.exists())

    def test_foreign_source_links_are_not_accepted(self):
        from .models import Order,CRMLead
        plan,stop=approved_plan(self)
        order=Order.objects.create(site=self.other,partner=self.customer,kind='Sale',created_by=self.rep)
        lead=CRMLead.objects.create(number='FIELD-FOREIGN',site=self.other,owner=self.outsider,business_name='Foreign prospect',mobile='9876543210',partner=self.customer,created_by=self.outsider)
        for data in [dict(order=order),dict(lead=lead)]:
            with self.assertRaises(ValidationError):beats.record_stop(self.rep,stop.pk,outcome_data(self,**data))
        self.assertEqual(BeatStop.objects.get(pk=stop.pk).status,'planned')

    def test_work_queues_match_owner_review_and_followup_states(self):
        from .insights import my_queue
        plan,stop=plan_draft(self);beats.submit_plan(self.rep,plan.pk,'Ready')
        self.assertTrue(any(i['label']=='Beat plans to approve' for i in my_queue(self.checker)))
        beats.decide_plan(self.checker,plan.pk,True,'Approved')
        self.assertTrue(any(i['label']=='Daily routes with visits due' for i in my_queue(self.rep)))
        beats.record_stop(self.rep,stop.pk,outcome_data(self,outcome='promise',promised_amount='20',promised_on=self.today,next_action_on=self.today))
        self.assertTrue(any(i['label']=='Field follow-ups due' for i in my_queue(self.rep)))
        self.assertTrue(any(i['label']=='Field promises to review' for i in my_queue(self.checker)))
        beats.publish_promise(self.checker,stop.pk,'Confirmed')
        self.assertFalse(any(i['label']=='Field promises to review' for i in my_queue(self.checker)))
