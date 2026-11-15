"""BOM and weighed-dispatch reviews preserve source evidence and scoped independence."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from django.contrib.auth.models import User,Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts import delegation as D,approval_work as AW,work_handoff as H
from accounts.models import ApprovalDelegationUse,UserProfile,ApprovalRouting
from accounts.test_support import force_login_recent
from . import production as PR,logistics as L,test_logistics as logistics_fixture
from .models import Product,Recipe,SiteAccess
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class RecipeApprovalTests(TestCase):
    def setUp(self):
        seed_business_data(self)
        self.admin=User.objects.create_superuser('bom-admin','qa@example.com','Fictional-password-816!')
        self.deputy=User.objects.create_user('bom-deputy');self.deputy.user_permissions.add(Permission.objects.get(codename='view_recipe'))
        self.recipe=PR.save_recipe(self.maker,self.bulk,[(self.raw,Decimal(1),Decimal(2))])

    def grant(self,principal=None):
        now=timezone.now()
        return D.create(self.admin,principal or self.checker,self.deputy,'approve_recipe',None,now,now+timedelta(days=1),'BOM review cover')

    def test_delegated_approval_retires_prior_version_and_keeps_actual_actor(self):
        PR.approve_recipe(self.checker,self.recipe.pk)
        new=PR.save_recipe(self.maker,self.bulk,[(self.raw,Decimal('1.1'),Decimal(2))])
        self.grant();PR.approve_recipe(self.deputy,new.pk)
        self.recipe.refresh_from_db();new.refresh_from_db()
        self.assertEqual(self.recipe.status,'retired');self.assertEqual(new.approved_by_id,self.deputy.pk)
        self.assertEqual(PR.current_recipe(self.bulk).pk,new.pk)
        self.assertEqual(ApprovalDelegationUse.objects.get().reference,f'recipe:{new.pk}')
        self.assertFalse(self.deputy.has_perm('erp.approve_recipe'))
        with self.assertRaises(ValidationError):PR.approve_recipe(self.deputy,new.pk)
        self.assertEqual(ApprovalDelegationUse.objects.count(),1)

    def test_maker_principal_and_invalid_material_rollback_all_changes(self):
        grant=self.grant(self.maker)
        with self.assertRaises((PermissionDenied,ValidationError)):PR.approve_recipe(self.deputy,self.recipe.pk)
        D.revoke(self.admin,grant.pk,'Replace maker grant');self.grant()
        Product.objects.filter(pk=self.raw.pk).update(active=False)
        with self.assertRaises(ValidationError):PR.approve_recipe(self.deputy,self.recipe.pk)
        self.assertFalse(ApprovalDelegationUse.objects.exists());self.recipe.refresh_from_db();self.assertEqual(self.recipe.status,'draft')

    def test_routing_and_sensitive_confirmation_preserve_draft(self):
        self.grant();task=AW.source('recipe',self.recipe.pk)
        self.assertEqual(AW.source_url(task),reverse('recipes'));self.assertEqual(task['scope_label'],'Company recipe')
        H.assign_approval(self.admin,'recipe',self.recipe.pk,self.deputy,'Independent BOM reviewer',uuid4())
        self.assertEqual(len(H.assigned_pending(self.deputy)),1)
        self.client.force_login(self.deputy)
        response=self.client.post(reverse('recipe_approve',args=[self.recipe.pk]))
        self.assertIn(reverse('reauthenticate'),response.url)
        self.recipe.refresh_from_db();self.assertEqual(self.recipe.status,'draft')
        force_login_recent(self.client,self.deputy)
        self.assertContains(self.client.get(reverse('recipes')),'Approve')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_expiry_and_principal_read_loss_invalidate_grant(self):
        grant=self.grant()
        from unittest.mock import patch
        with patch('accounts.delegation.timezone.now',return_value=grant.ends_at):
            with self.assertRaises(PermissionDenied):PR.approve_recipe(self.deputy,self.recipe.pk)
        self.checker.user_permissions.remove(Permission.objects.get(codename='view_recipe'))
        with self.assertRaises(PermissionDenied):PR.approve_recipe(self.deputy,self.recipe.pk)
        self.assertFalse(ApprovalDelegationUse.objects.exists())


@override_settings(EMAIL_OTP_ENABLED=False)
class WeightApprovalTests(TestCase):
    @classmethod
    def setUpTestData(cls):logistics_fixture.LogisticsTests.setUpTestData.__func__(cls)

    def setUp(self):
        logistics_fixture.LogisticsTests.setUp(self)
        self.dispatch=logistics_fixture.LogisticsTests.run_to_weighed(self,qty=400,gross=29600)
        self.deputy=User.objects.create_user('weight-deputy');self.deputy.user_permissions.add(Permission.objects.get(codename='view_dispatch'))
        SiteAccess.objects.create(user=self.deputy,site=self.site)

    def grant(self,principal=None):
        now=timezone.now()
        return D.create(self.admin,principal or self.admin,self.deputy,'approve_weight_exception',self.site,now,now+timedelta(days=1),'Independent weighbridge review cover')

    def test_delegated_weight_review_preserves_readings_and_denies_repeated_decision(self):
        self.grant();before=list(self.dispatch.weighments.values())
        L.approve_exception(self.deputy,self.dispatch.pk,'Recorded tolerance variance reviewed')
        self.dispatch.refresh_from_db();self.assertEqual(self.dispatch.exception_approved_by_id,self.deputy.pk)
        self.assertEqual(list(self.dispatch.weighments.values()),before)
        with self.assertRaises(ValidationError):L.approve_exception(self.deputy,self.dispatch.pk,'Cannot replace finding')
        self.assertEqual(ApprovalDelegationUse.objects.count(),1)
        self.assertIsNone(AW.source('weight_exception',self.dispatch.pk))

    def test_historical_tare_and_gross_operators_and_creator_cannot_lend_authority(self):
        self.wb.user_permissions.add(Permission.objects.get(codename='approve_weight_exception'))
        L.weigh(self.sup,self.dispatch.pk,'gross',29650,'scale','Independent reweigh capture')
        self.dispatch.refresh_from_db()
        self.assertIn(self.wb.pk,L.exception_makers(self.dispatch))
        grant=self.grant(self.wb)
        with self.assertRaises((PermissionDenied,ValidationError)):L.approve_exception(self.deputy,self.dispatch.pk,'Old operator principal denied')
        D.revoke(self.admin,grant.pk,'Replace contributor grant')
        self.planner.user_permissions.add(Permission.objects.get(codename='approve_weight_exception'))
        self.grant(self.planner)
        with self.assertRaises((PermissionDenied,ValidationError)):L.approve_exception(self.deputy,self.dispatch.pk,'Creator principal denied')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_reweigh_invalidates_approval_and_requires_new_reading_reference(self):
        self.grant();L.approve_exception(self.deputy,self.dispatch.pk,'First reviewed reading')
        reference=ApprovalDelegationUse.objects.get().reference
        previous_readings=f'{self.dispatch.tare.pk}:{self.dispatch.gross.pk}'
        L.weigh(self.sup,self.dispatch.pk,'gross',29700,'scale','New retained weighing')
        self.dispatch.refresh_from_db();self.assertIsNone(self.dispatch.exception_approved_by_id)
        self.assertIsNotNone(AW.source('weight_exception',self.dispatch.pk))
        with self.assertRaises(ValidationError):L.approve_exception(self.deputy,self.dispatch.pk,'Stale approval form',expected_readings=previous_readings)
        self.assertEqual(ApprovalDelegationUse.objects.count(),1)
        L.approve_exception(self.deputy,self.dispatch.pk,'New reading independently reviewed')
        self.assertEqual(ApprovalDelegationUse.objects.count(),2)
        self.assertTrue(ApprovalDelegationUse.objects.exclude(reference=reference).exists())

    def test_invalid_note_site_loss_and_expiry_leave_no_use(self):
        grant=self.grant()
        with self.assertRaises(ValidationError):L.approve_exception(self.deputy,self.dispatch.pk,'')
        SiteAccess.objects.filter(user=self.deputy,site=self.site).delete()
        with self.assertRaises(PermissionDenied):L.approve_exception(self.deputy,self.dispatch.pk,'Lost source site')
        SiteAccess.objects.create(user=self.deputy,site=self.site)
        from unittest.mock import patch
        with patch('accounts.delegation.timezone.now',return_value=grant.ends_at):
            with self.assertRaises(PermissionDenied):L.approve_exception(self.deputy,self.dispatch.pk,'Expired grant')
        self.assertFalse(ApprovalDelegationUse.objects.exists())

    def test_qualified_backup_routing_moves_review_without_rewriting_readings(self):
        reviewer=logistics_fixture.user_with('weight-reviewer','approve_weight_exception')
        SiteAccess.objects.create(user=reviewer,site=self.site)
        H.assign_approval(self.admin,'weight_exception',self.dispatch.pk,reviewer,'Named independent checker',uuid4())
        UserProfile.objects.update_or_create(user=reviewer,defaults={'work_backup':self.admin})
        before=list(self.dispatch.weighments.values())
        with self.captureOnCommitCallbacks(execute=True):
            reviewer.is_active=False;reviewer.save(update_fields=['is_active'])
        self.assertEqual(ApprovalRouting.objects.get(kind='weight_exception',object_id=self.dispatch.pk).assignee_id,self.admin.pk)
        self.dispatch.refresh_from_db();self.assertIsNone(self.dispatch.exception_approved_by_id)
        self.assertEqual(list(self.dispatch.weighments.values()),before)

    def test_web_weight_review_requires_recent_identity_confirmation(self):
        self.client.force_login(self.admin)
        response=self.client.post(reverse('dispatch_action',args=[self.dispatch.pk,'approve']),{'note':'Review'})
        self.assertIn(reverse('reauthenticate'),response.url)
        self.dispatch.refresh_from_db();self.assertIsNone(self.dispatch.exception_approved_by_id)
