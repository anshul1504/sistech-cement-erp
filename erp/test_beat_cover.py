"""Independent finite execution cover retains original route and actual actors."""
from datetime import timedelta
from django.contrib.auth.models import User, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.test_support import force_login_recent
from accounts.models import UserProfile
from accounts import approval_work as AW
from . import beats, beat_cover
from .models import BeatExecutionCover, BeatPlan, BeatStop, SiteAccess
from .test_beats import seed_beats, approved_plan, outcome_data


@override_settings(EMAIL_OTP_ENABLED=False)
class ExecutionCoverTests(TestCase):
    def setUp(self):
        seed_beats(self)
        self.coverer=User.objects.create_user('cover-executor')
        self.coverer.user_permissions.set(self.rep.user_permissions.all())
        SiteAccess.objects.create(user=self.coverer,site=self.site)
        self.plan,self.stop=approved_plan(self)

    def propose(self):
        return beat_cover.propose(self.maker,self.plan.pk,self.coverer,timezone.now()+timedelta(days=1),'Temporary absence')

    def approve(self):
        obj=self.propose()
        return beat_cover.decide(self.checker,obj.pk,True,'Independent scope and evidence checked')

    def test_execution_retains_owner_approval_and_actual_actor(self):
        obj=self.approve()
        before=(self.plan.owner_id,self.plan.approved_by_id,self.plan.revision,self.plan.reviews.count())
        result=beats.record_stop(self.coverer,self.stop.pk,outcome_data(self))
        self.plan.refresh_from_db()
        self.assertEqual((self.plan.owner_id,self.plan.approved_by_id,self.plan.revision,self.plan.reviews.count()),before)
        self.assertEqual(result.recorded_by_id,self.coverer.pk)
        self.assertEqual(self.plan.status,'finished')
        self.assertTrue(beat_cover.executes(self.coverer,self.plan))
        with self.assertRaises(ValidationError):beats.record_stop(self.coverer,self.stop.pk,outcome_data(self))

    def test_pending_cover_grants_no_access_and_independence_is_enforced(self):
        obj=self.propose()
        for actor in [self.maker,self.coverer,self.rep]:
            with self.assertRaises(PermissionDenied):beat_cover.decide(actor,obj.pk,True,'Self review denied')
        with self.assertRaises(PermissionDenied):beats.record_stop(self.coverer,self.stop.pk,outcome_data(self))
        self.assertFalse(beats.scope(BeatPlan.objects.all(),self.coverer).exists())
        task=AW.source('beat_cover',obj.pk)
        self.assertFalse(AW.eligible(self.maker,task)[0])
        self.assertTrue(AW.eligible(self.checker,task)[0])
        self.assertTrue(beat_cover.reviewable(self.checker).filter(pk=obj.pk).exists())
        self.assertFalse(beat_cover.reviewable(self.maker).filter(pk=obj.pk).exists())
        self.assertEqual(AW.source_url(task),reverse('beat_cover_detail',args=[obj.pk]))

    def test_stale_work_cannot_be_approved_or_replayed(self):
        obj=self.propose()
        beats.record_stop(self.rep,self.stop.pk,outcome_data(self,next_action_on=self.today))
        with self.assertRaises(ValidationError):beat_cover.decide(self.checker,obj.pk,True,'Old evidence')
        obj.refresh_from_db();self.assertEqual(obj.status,'submitted')
        beat_cover.decide(self.checker,obj.pk,False,'Route evidence changed')
        with self.assertRaises(ValidationError):beat_cover.decide(self.checker,obj.pk,True,'Replay')
        fresh=self.propose()
        beat_cover.decide(self.checker,fresh.pk,True,'Current follow-up checked')
        beats.close_followup(self.coverer,self.stop.pk,'Follow-up completed by covering rep')
        self.stop.refresh_from_db();self.assertEqual(self.stop.followed_by_id,self.coverer.pk)

    def test_cover_does_not_grant_route_editing(self):
        self.approve()
        for action in [lambda:beats.cancel_plan(self.coverer,self.plan.pk,'Cannot cancel'),
                       lambda:beats.add_stop(self.coverer,self.plan.pk,{}),
                       lambda:beats.submit_plan(self.coverer,self.plan.pk,'Cannot submit')]:
            with self.assertRaises(PermissionDenied):action()

    def test_expiry_revocation_and_site_loss_remove_access(self):
        obj=self.approve()
        BeatExecutionCover.objects.filter(pk=obj.pk).update(ends_at=timezone.now()-timedelta(seconds=1),starts_at=timezone.now()-timedelta(days=1))
        self.assertFalse(beat_cover.executes(self.coverer,self.plan))
        with self.assertRaises(PermissionDenied):beats.record_stop(self.coverer,self.stop.pk,outcome_data(self))
        obj=self.approve()
        SiteAccess.objects.filter(user=self.coverer,site=self.site).delete()
        with self.assertRaises(PermissionDenied):beats.record_stop(self.coverer,self.stop.pk,outcome_data(self))
        SiteAccess.objects.create(user=self.coverer,site=self.site)
        beat_cover.revoke(self.maker,obj.pk,'Coverage ended')
        self.assertFalse(beat_cover.executes(self.coverer,self.plan))

    def test_invalid_recipient_window_and_overlap(self):
        for actor,end in [(self.rep,timezone.now()+timedelta(days=1)),(self.coverer,timezone.now()-timedelta(seconds=1))]:
            with self.assertRaises(ValidationError):beat_cover.propose(self.maker,self.plan.pk,actor,end,'Reason')
        with self.assertRaises(PermissionDenied):beat_cover.propose(self.rep,self.plan.pk,self.coverer,timezone.now()+timedelta(days=1),'Reason')
        self.propose()
        with self.assertRaises(ValidationError):self.propose()
        UserProfile.objects.update_or_create(user=self.coverer,defaults={'access_expires_at':timezone.now()-timedelta(seconds=1)})
        self.assertFalse(beat_cover.executes(self.coverer,self.plan))

    def test_scoped_http_and_sensitive_confirmation(self):
        obj=self.approve()
        self.client.force_login(self.maker)
        response=self.client.post(reverse('beat_cover_action',args=[obj.pk,'revoke']),{'note':'Review required'})
        self.assertIn(reverse('reauthenticate'),response.url)
        force_login_recent(self.client,self.coverer)
        self.assertContains(self.client.get(reverse('beat_detail',args=[self.plan.pk])),'Record visit')
        self.assertContains(self.client.get(reverse('beat_cover_detail',args=[obj.pk])),'Cover executor')
        self.assertEqual(self.client.get(reverse('beat_detail',args=[self.plan.pk])).status_code,200)
        force_login_recent(self.client,self.outsider)
        self.assertEqual(self.client.get(reverse('beat_cover_detail',args=[obj.pk])).status_code,404)

    def test_unavailable_attention_returns_when_cover_expires(self):
        from accounts.work_handoff import inventory
        self.assertTrue(any(r['kind']=='beat' for r in inventory(self.rep,None)))
        obj=self.approve()
        self.assertFalse(any(r['kind']=='beat' for r in inventory(self.rep,None)))
        beat_cover.revoke(self.maker,obj.pk,'Cover withdrawn')
        self.assertTrue(any(r['kind']=='beat' for r in inventory(self.rep,None)))

    def test_partial_route_and_prior_visit_evidence_are_preserved(self):
        from .models import Partner
        from .test_beats import plan_draft
        beats.cancel_plan(self.rep,self.plan.pk,'Rebuild fictional two-stop route')
        self.plan,self.stop=plan_draft(self)
        customer=Partner.objects.create(code='COVER-D2',name='Cover second dealer',kind='Dealer')
        assignment=beats.assign(self.maker,dict(territory=self.area,partner=customer,owner=self.rep,starts_on=self.today,ends_on=None,note='Second stop'))
        second=beats.add_stop(self.rep,self.plan.pk,dict(assignment=assignment,sequence=2,purpose='visit',instructions='Second visit'))
        beats.submit_plan(self.rep,self.plan.pk,'Two-stop route checked')
        beats.decide_plan(self.checker,self.plan.pk,True,'Two-stop route approved')
        beats.record_stop(self.rep,self.stop.pk,outcome_data(self,next_action_on=self.today))
        self.stop.refresh_from_db();retained=(self.stop.recorded_by_id,self.stop.recorded_at,self.stop.note,self.stop.snapshot)
        self.approve()
        self.rep.is_active=False;self.rep.save(update_fields=['is_active'])
        beats.record_stop(self.coverer,second.pk,outcome_data(self))
        beats.close_followup(self.coverer,self.stop.pk,'Cover closed retained visit follow-up')
        self.stop.refresh_from_db();second.refresh_from_db()
        self.assertEqual((self.stop.recorded_by_id,self.stop.recorded_at,self.stop.note,self.stop.snapshot),retained)
        self.assertEqual(second.recorded_by_id,self.coverer.pk)

    def test_approved_cover_loses_authority_after_access_expiry_or_revision_change(self):
        self.approve()
        UserProfile.objects.update_or_create(user=self.coverer,defaults={'access_expires_at':timezone.now()-timedelta(seconds=1)})
        with self.assertRaises(PermissionDenied):beats.record_stop(self.coverer,self.stop.pk,outcome_data(self))
        UserProfile.objects.filter(user=self.coverer).update(access_expires_at=None)
        BeatPlan.objects.filter(pk=self.plan.pk).update(revision=self.plan.revision+1)
        self.plan.refresh_from_db()
        self.assertFalse(beat_cover.executes(self.coverer,self.plan))

    def test_delegated_independent_review_records_actual_actor(self):
        from accounts import delegation
        from accounts.models import ApprovalDelegationUse
        admin=User.objects.create_superuser('cover-admin','qa@example.com','Fictional-password-898!')
        deputy=User.objects.create_user('cover-deputy')
        deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_beatplan','view_team_beats']))
        SiteAccess.objects.create(user=deputy,site=self.site)
        now=timezone.now()
        grant=delegation.create(admin,self.checker,deputy,'approve_beatplan',self.site,now,now+timedelta(days=1),'Review cover while away')
        obj=self.propose()
        beat_cover.decide(deputy,obj.pk,True,'Independent delegated cover review')
        obj.refresh_from_db();self.assertEqual(obj.reviewed_by_id,deputy.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=deputy,reference=self.plan.number).exists())
