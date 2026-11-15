"""Recognition review delegation preserves makers and reward delivery evidence."""
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
from . import recognition as RC
from .models import RecognitionProgram,RecognitionLevel,RecognitionAward,Influencer
from .test_support import seed_business_data
from .test_schemes import invoice_fixture


@override_settings(EMAIL_OTP_ENABLED=False)
class RecognitionApprovalTests(TestCase):
    def setUp(self):
        seed_business_data(self);self.today=timezone.localdate()
        self.admin=User.objects.create_superuser('recognition-admin','qa@example.com','Fictional-password-931!')
        self.deputy=User.objects.create_user('recognition-deputy')
        self.deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_recognitionprogram','view_influencerusage']))
        invoice_fixture(self,[(self.bag,Decimal(100))])
        self.program=RecognitionProgram.objects.create(name='Fictional star programme',audience='dealer',starts_on=self.today-timedelta(days=2),ends_on=self.today,created_by=self.maker)
        RecognitionLevel.objects.create(program=self.program,level='national',winners=1,minimum=0,reward='Fictional trophy')
        RC.rank(self.maker,self.program.pk);self.program.refresh_from_db()

    def grant(self,permission='publish_recognitionprogram',principal=None):
        now=timezone.now()
        return D.create(self.admin,principal or self.checker,self.deputy,permission,None,now,now+timedelta(days=1),'Recognition review cover')

    def test_delegated_publication_keeps_actual_actor_and_frozen_awards(self):
        grant=self.grant();awards=list(self.program.awards.values())
        RC.publish(self.deputy,self.program.pk,True,'Independent ranked winner review')
        self.program.refresh_from_db();self.assertEqual(self.program.published_by_id,self.deputy.pk)
        self.assertEqual(list(self.program.awards.values()),awards)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=self.deputy,reference=f'STAR-{self.program.pk:05d}').exists())
        self.assertFalse(self.deputy.has_perm('erp.publish_recognitionprogram'))
        with self.assertRaises(ValidationError):RC.rank(self.maker,self.program.pk)
        with self.assertRaises(ValidationError):RC.publish(self.deputy,self.program.pk,True,'Replay')
        self.assertEqual(ApprovalDelegationUse.objects.count(),1)
        with self.assertRaises(PermissionDenied):RC.fulfil(self.deputy,self.program.awards.get().pk,'Publish grant is not reward delivery authority')

    def test_creator_and_previous_rankers_remain_makers_after_rerank_and_sendback(self):
        RC.rank(self.checker,self.program.pk);self.program.refresh_from_db()
        for actor in [self.maker,self.checker]:
            self.assertFalse(RC.can_publish(actor,self.program))
            with self.assertRaises(ValidationError):RC.publish(actor,self.program.pk,True,'Self approval')
        RC.publish(self.admin,self.program.pk,False,'Review source evidence again')
        RC.rank(self.admin,self.program.pk);self.program.refresh_from_db()
        self.assertTrue({self.maker.pk,self.checker.pk,self.admin.pk}.issubset(RC.makers(self.program)))

    def test_maker_principal_is_excluded_and_sendback_failure_rolls_back(self):
        grant=self.grant(principal=self.maker)
        with self.assertRaises((PermissionDenied,ValidationError)):RC.publish(self.deputy,self.program.pk,True,'Maker proxy')
        D.revoke(self.admin,grant.pk,'Replace maker grant');self.grant()
        with self.assertRaises(ValidationError):RC.publish(self.deputy,self.program.pk,False,'')
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        self.program.refresh_from_db();self.assertEqual(self.program.status,'ranked');self.assertEqual(self.program.awards.count(),1)

    def test_usage_verification_has_separate_authority_and_retains_actual_deputy(self):
        influencer=Influencer.objects.create(code='RECO-QA',name='Fictional mason',kind='Mason')
        usage=RC.record_usage(self.maker,influencer,self.customer,self.bag,10,self.today,'QA-USAGE')
        self.grant()
        with self.assertRaises(PermissionDenied):RC.verify_usage(self.deputy,usage.pk,True)
        self.grant('verify_influencerusage')
        task=AW.source('recognition_usage',usage.pk)
        self.assertTrue(AW.eligible(self.deputy,task)[0]);self.assertEqual(AW.source_url(task),reverse('influencer_usage'))
        RC.verify_usage(self.deputy,usage.pk,True,'Dealer evidence independently checked')
        usage.refresh_from_db();self.assertEqual(usage.verified_by_id,self.deputy.pk)
        self.assertIsNone(AW.source('recognition_usage',usage.pk))

    def test_routing_and_disable_backup_move_work_without_publishing(self):
        backup=User.objects.create_superuser('recognition-backup','backup@example.com','Fictional-password-917!')
        task=AW.source('recognition_publication',self.program.pk)
        self.assertEqual(task['scope_label'],'Company recognition')
        self.assertFalse(AW.eligible(self.maker,task)[0])
        H.assign_approval(self.admin,'recognition_publication',self.program.pk,self.checker,'Accountable independent publisher',uuid4())
        UserProfile.objects.update_or_create(user=self.checker,defaults={'work_backup':backup})
        with self.captureOnCommitCallbacks(execute=True):
            self.checker.is_active=False;self.checker.save(update_fields=['is_active'])
        self.assertEqual(ApprovalRouting.objects.get(kind='recognition_publication',object_id=self.program.pk).assignee_id,backup.pk)
        self.program.refresh_from_db();self.assertEqual(self.program.status,'ranked')
        RC.publish(backup,self.program.pk,True,'Qualified backup review')
        self.assertEqual(H.assigned_pending(backup),[])

    def test_expiry_and_principal_read_scope_loss_stop_delegation(self):
        grant=self.grant()
        from unittest.mock import patch
        with patch('accounts.delegation.timezone.now',return_value=grant.ends_at):
            self.assertFalse(RC.can_publish(self.deputy,self.program))
            with self.assertRaises(PermissionDenied):RC.publish(self.deputy,self.program.pk,True,'Expired grant')
        self.checker.user_permissions.remove(Permission.objects.get(codename='view_recognitionprogram'))
        self.assertFalse(RC.can_publish(self.deputy,self.program))
        with self.assertRaises(PermissionDenied):RC.publish(self.deputy,self.program.pk,True,'Principal read scope removed')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_delivery_receipt_retry_keeps_actor_timestamp_and_note(self):
        RC.publish(self.checker,self.program.pk,True,'Independent publication')
        award=self.program.awards.get()
        RC.fulfil(self.admin,award.pk,'Trophy handed over at fictional annual event')
        award.refresh_from_db();before=(award.fulfilled_by_id,award.fulfilled_at,award.fulfilment_note)
        RC.fulfil(self.admin,award.pk,before[2]);award.refresh_from_db()
        self.assertEqual((award.fulfilled_by_id,award.fulfilled_at,award.fulfilment_note),before)
        with self.assertRaises(ValidationError):RC.fulfil(self.admin,award.pk,'Changed evidence cannot overwrite receipt')
        self.assertEqual(award.fulfilled_by_id,self.admin.pk)

    def test_web_review_needs_confirmation_and_award_must_belong_to_route(self):
        self.client.force_login(self.checker)
        response=self.client.post(reverse('recognition_action',args=[self.program.pk,'publish']),{'note':'Review'})
        self.assertIn(reverse('reauthenticate'),response.url)
        RC.publish(self.checker,self.program.pk,True,'Independent publication')
        other=RecognitionProgram.objects.create(name='Different programme',audience='dealer',starts_on=self.today,ends_on=self.today,created_by=self.maker)
        force_login_recent(self.client,self.admin)
        response=self.client.post(reverse('recognition_action',args=[other.pk,'fulfil']),{'award':self.program.awards.get().pk,'note':'Forged route'})
        self.assertEqual(response.status_code,404)
        self.assertFalse(self.program.awards.get().fulfilled)

    def test_migration_retains_known_legacy_makers_without_inventing_delivery_actor(self):
        import importlib
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        self.program.contributors.clear()
        RecognitionProgram.objects.filter(pk=self.program.pk).update(ranked_by=self.checker)
        award=self.program.awards.get();award.fulfilled=True;award.fulfilment_note='Legacy delivery evidence';award.save()
        migration=importlib.import_module('erp.migrations.0048_recognitionaward_fulfilled_at_and_more')
        migration.retain_known_contributors(apps,SimpleNamespace(connection=connection))
        self.assertEqual(set(self.program.contributors.values_list('pk',flat=True)),{self.maker.pk,self.checker.pk})
        award.refresh_from_db();self.assertIsNone(award.fulfilled_by_id);self.assertIsNone(award.fulfilled_at)
        self.assertEqual(award.fulfilment_note,'Legacy delivery evidence')
