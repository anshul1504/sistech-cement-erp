"""Dated transfer atomically preserves original ownership and future route lineage."""
from datetime import timedelta
from uuid import uuid4
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.test_support import force_login_recent
from . import beats,dealer_transfer
from .models import DealerTerritoryAssignment,DealerOwnershipTransfer,BeatPlan,BeatStop,Partner,SiteAccess
from .test_beats import seed_beats,approved_plan,plan_draft,outcome_data


@override_settings(EMAIL_OTP_ENABLED=False)
class DealerTransferTests(TestCase):
    def setUp(self):
        seed_beats(self)
        self.recipient=User.objects.create_user('transfer-recipient')
        self.recipient.user_permissions.set(self.rep.user_permissions.all());SiteAccess.objects.create(user=self.recipient,site=self.site)
        self.effective=self.today+timedelta(days=1)

    def preview(self,source=None):
        return dealer_transfer.preview(self.maker,(source or self.assignment).pk,self.recipient,self.effective)

    def move(self,preview=None,key=None,source=None):
        source=source or self.assignment
        preview=preview or self.preview(source)
        return dealer_transfer.transfer(self.maker,source.pk,self.recipient,self.effective,'Reviewed commercial handover',key or uuid4(),preview['fingerprint'])

    def test_inclusive_date_split_preserves_today_and_moves_future_routes(self):
        today,visit=approved_plan(self)
        beats.record_stop(self.rep,visit.pk,outcome_data(self,next_action_on=self.today))
        future,old=approved_plan(self,self.effective)
        old.refresh_from_db();snapshot=old.snapshot
        preview=self.preview();self.assertEqual(len(preview['stops']),1)
        receipt=self.move(preview)
        self.assignment.refresh_from_db();today.refresh_from_db();visit.refresh_from_db();old.refresh_from_db();future.refresh_from_db()
        self.assertEqual(self.assignment.ends_on,self.today)
        self.assertEqual(receipt.replacement.starts_on,self.effective)
        self.assertIsNone(receipt.replacement.ends_on)
        self.assertEqual(visit.recorded_by_id,self.rep.pk)
        self.assertEqual(today.owner_id,self.rep.pk)
        self.assertEqual(old.status,'reassigned');self.assertEqual(old.snapshot,snapshot)
        self.assertEqual(old.replacement_stop.assignment_id,receipt.replacement_id)
        self.assertEqual(old.replacement_stop.plan.owner_id,self.recipient.pk)
        self.assertEqual(old.replacement_stop.plan.status,'draft')
        self.assertEqual(future.status,'cancelled')
        self.assertTrue(future.reviews.filter(action='Approved').exists())
        with self.assertRaises(ValidationError):beats.record_stop(self.recipient,old.replacement_stop_id,outcome_data(self))
        replacement=old.replacement_stop.plan
        beats.submit_plan(self.recipient,replacement.pk,'Transferred route reviewed and resubmitted')
        with self.assertRaises(ValidationError):beats.decide_plan(self.maker,replacement.pk,True,'Transfer actor cannot check')
        with self.assertRaises((ValidationError,PermissionDenied)):beats.decide_plan(self.rep,replacement.pk,True,'Former owner cannot check')
        beats.decide_plan(self.checker,replacement.pk,True,'Independent replacement route review')

    def test_partial_source_and_existing_destination_require_new_approval(self):
        source,old=plan_draft(self,self.effective)
        other=Partner.objects.create(code='TRANSFER-OTHER',name='Another assigned dealer',kind='Dealer')
        assignment=beats.assign(self.maker,dict(territory=self.area,partner=other,owner=self.rep,starts_on=self.today,ends_on=None,note='Other retained dealer'))
        retained=beats.add_stop(self.rep,source.pk,dict(assignment=assignment,sequence=2,purpose='visit',instructions='Retained route stop'))
        beats.submit_plan(self.rep,source.pk,'Two-stop route');beats.decide_plan(self.checker,source.pk,True,'Reviewed original')
        destination=beats.save_plan(self.recipient,dict(site=self.site,owner=self.recipient,on=self.effective,title='Existing recipient route'))
        receipt=self.move()
        source.refresh_from_db();retained.refresh_from_db();old.refresh_from_db();destination.refresh_from_db()
        self.assertEqual(source.status,'draft');self.assertEqual(retained.status,'planned')
        self.assertIsNone(source.approved_by_id)
        self.assertEqual(old.replacement_stop.plan_id,destination.pk)
        beats.submit_plan(self.rep,source.pk,'Remaining dealer route checked')
        beats.decide_plan(self.checker,source.pk,True,'Remaining stop reviewed')
        self.assertEqual(source.stops.count(),2)
        self.assertEqual(receipt.evidence['moved_stops'][0]['source_stop'],old.pk)

    def test_future_assignment_superseded_without_invalid_date_rewrite(self):
        beats.end_assignment(self.maker,self.assignment.pk,self.today,'End present ownership')
        future=beats.assign(self.maker,dict(territory=self.area,partner=self.customer,owner=self.rep,starts_on=self.effective,ends_on=self.effective+timedelta(days=7),note='Future window'))
        receipt=self.move(source=future)
        future.refresh_from_db()
        self.assertIsNotNone(future.superseded_at)
        self.assertEqual(future.starts_on,self.effective)
        self.assertEqual(future.ends_on,self.effective+timedelta(days=7))
        self.assertEqual(receipt.replacement.ends_on,future.ends_on)
        form=__import__('erp.beat_forms',fromlist=['StopForm']).StopForm(plan=beats.save_plan(self.rep,dict(site=self.site,owner=self.rep,on=self.effective,title='Check assignment choices')))
        self.assertFalse(form.fields['assignment'].queryset.filter(pk=future.pk).exists())
        with self.assertRaises(ValidationError):beats.end_assignment(self.maker,future.pk,self.effective,'Cannot end superseded')

    def test_stale_preview_and_duplicate_request_are_safe(self):
        plan,stop=approved_plan(self,self.effective)
        preview=self.preview()
        BeatPlan.objects.filter(pk=plan.pk).update(title='Changed route after preview')
        with self.assertRaises(ValidationError):self.move(preview)
        self.assertEqual(DealerOwnershipTransfer.objects.count(),0)
        stop.refresh_from_db();self.assertEqual(stop.status,'planned')
        preview=self.preview();key=uuid4();receipt=self.move(preview,key)
        self.assertEqual(self.move(preview,key).pk,receipt.pk)
        self.assertEqual(DealerOwnershipTransfer.objects.count(),1)
        with self.assertRaises(ValidationError):
            dealer_transfer.transfer(self.maker,self.assignment.pk,self.recipient,self.effective,'Changed reason',key,preview['fingerprint'])
        with self.assertRaises(ValidationError):self.move(preview)

    def test_denied_dates_roles_and_recipient_scope_leave_no_changes(self):
        for on in [self.today,self.today-timedelta(days=1)]:
            with self.assertRaises(ValidationError):dealer_transfer.preview(self.maker,self.assignment.pk,self.recipient,on)
        with self.assertRaises(PermissionDenied):dealer_transfer.preview(self.rep,self.assignment.pk,self.recipient,self.effective)
        with self.assertRaises(PermissionDenied):dealer_transfer.preview(self.outsider,self.assignment.pk,self.recipient,self.effective)
        with self.assertRaises(PermissionDenied):dealer_transfer.preview(self.maker,self.assignment.pk,self.outsider,self.effective)
        self.recipient.is_active=False;self.recipient.save(update_fields=['is_active'])
        with self.assertRaises(ValidationError):self.preview()
        self.assertEqual(DealerOwnershipTransfer.objects.count(),0)

    def test_future_recorded_evidence_blocks_transfer(self):
        plan,stop=approved_plan(self,self.effective)
        BeatStop.objects.filter(pk=stop.pk).update(status='missed',note='Fictional inconsistent future evidence',recorded_by=self.rep,recorded_at=timezone.now())
        with self.assertRaises(ValidationError):self.preview()
        self.assignment.refresh_from_db();self.assertIsNone(self.assignment.ends_on)

    def test_preview_confirmation_http_and_history_are_scoped(self):
        url=reverse('dealer_transfer_new',args=[self.assignment.pk])
        self.client.force_login(self.maker)
        self.assertIn(reverse('reauthenticate'),self.client.post(url,{}).url)
        force_login_recent(self.client,self.maker)
        data=dict(recipient=self.recipient.pk,effective_on=str(self.effective),reason='Reviewed browser handover',request_key=str(uuid4()),phase='preview')
        response=self.client.post(url,data)
        self.assertContains(response,'Confirm reviewed transfer')
        self.assertEqual(DealerOwnershipTransfer.objects.count(),0)
        data.update(phase='confirm',fingerprint=response.context['preview']['fingerprint'])
        response=self.client.post(url,data)
        receipt=DealerOwnershipTransfer.objects.get()
        self.assertRedirects(response,reverse('dealer_transfer_detail',args=[receipt.pk]))
        self.assertContains(self.client.get(response.url),'Reviewed browser handover')
        force_login_recent(self.client,self.outsider)
        self.assertEqual(self.client.get(response.url).status_code,404)
        force_login_recent(self.client,self.recipient)
        self.assertEqual(self.client.get(response.url).status_code,403)

    def test_destination_created_after_preview_requires_fresh_confirmation(self):
        plan,stop=approved_plan(self,self.effective)
        preview=self.preview()
        beats.save_plan(self.recipient,dict(site=self.site,owner=self.recipient,on=self.effective,title='New destination after preview'))
        with self.assertRaises(ValidationError):self.move(preview)
        self.assignment.refresh_from_db();stop.refresh_from_db()
        self.assertIsNone(self.assignment.ends_on);self.assertEqual(stop.status,'planned')
        self.assertEqual(DealerOwnershipTransfer.objects.count(),0)

    def test_route_write_authority_is_required_when_transfer_moves_stops(self):
        from django.contrib.auth.models import Permission
        plan,stop=approved_plan(self,self.effective)
        self.maker.user_permissions.remove(Permission.objects.get(codename='change_beatplan'))
        self.maker=User.objects.get(pk=self.maker.pk)
        with self.assertRaises(PermissionDenied):self.preview()
        self.assertEqual(DealerOwnershipTransfer.objects.count(),0)

    def test_unexpected_review_failure_rolls_back_entire_transfer(self):
        from unittest.mock import patch
        plan,stop=approved_plan(self,self.effective)
        before=(DealerTerritoryAssignment.objects.count(),BeatPlan.objects.count(),BeatStop.objects.count())
        with patch('erp.dealer_transfer.beats._review',side_effect=RuntimeError('Fictional storage failure')):
            with self.assertRaises(RuntimeError):self.move()
        self.assignment.refresh_from_db();plan.refresh_from_db();stop.refresh_from_db()
        self.assertIsNone(self.assignment.ends_on);self.assertEqual(plan.status,'approved');self.assertEqual(stop.status,'planned')
        self.assertIsNone(stop.replacement_stop_id)
        self.assertEqual((DealerTerritoryAssignment.objects.count(),BeatPlan.objects.count(),BeatStop.objects.count()),before)
        self.assertFalse(DealerOwnershipTransfer.objects.exists())


    def test_recipient_roles_are_reloaded_after_preview(self):
        from django.contrib.auth.models import Permission
        preview=self.preview()
        self.recipient.user_permissions.remove(Permission.objects.get(codename='record_beatstop'))
        with self.assertRaises(ValidationError):self.move(preview)
        self.assertFalse(DealerOwnershipTransfer.objects.exists())
