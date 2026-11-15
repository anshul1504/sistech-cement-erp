"""Out-of-order BOM approval cannot roll back effective production standards."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import User,Permission
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.utils import timezone
from django.urls import reverse
from accounts.test_support import force_login_recent
from accounts import approval_work as AW,delegation as D
from accounts.models import ApprovalDelegationUse
from . import production as PR
from .models import Recipe,Production
from .test_support import seed_business_data


@override_settings(EMAIL_OTP_ENABLED=False)
class RecipeEffectivityTests(TestCase):
    def setUp(self):seed_business_data(self);self.today=timezone.localdate()

    def draft(self,on=None,quantity=1):
        return PR.save_recipe(self.maker,self.bulk,[(self.raw,Decimal(quantity),Decimal(2))],effective_from=on or self.today)

    def approve(self,r):return PR.approve_recipe(self.checker,r.pk)

    def test_late_old_draft_cannot_retire_new_current_version(self):
        old=self.draft();current=self.approve(self.draft(quantity=2))
        with self.assertRaises(ValidationError):self.approve(old)
        current.refresh_from_db();old.refresh_from_db()
        self.assertEqual((current.status,old.status),('approved','draft'))
        self.assertEqual(PR.current_recipe(self.bulk).pk,current.pk)
        self.assertFalse(PR.can_approve_recipe(self.checker,old));self.assertIsNone(AW.source('recipe',old.pk))
        force_login_recent(self.client,self.checker)
        response=self.client.get(reverse('recipes'))
        self.assertContains(response,'Superseded draft')
        self.assertNotContains(response,'>Approve</button>')

    def test_earlier_window_can_be_approved_before_newer_future_version_starts(self):
        current=self.draft();future=self.approve(self.draft(self.today+timedelta(days=10),2))
        self.approve(current)
        self.assertEqual(PR.current_recipe(self.bulk).pk,current.pk)
        self.assertEqual(PR.current_recipe(self.bulk,self.today+timedelta(days=10)).pk,future.pk)

    def test_new_current_revision_supersedes_older_scheduled_formula_without_rebinding_batches(self):
        current=self.approve(self.draft())
        day=self.today+timedelta(days=10);future=self.approve(self.draft(day,2))
        planned=PR.create_order(self.maker,self.site,self.bulk,10,'A',planned_date=day)
        latest=self.approve(self.draft(quantity=3))
        current.refresh_from_db();future.refresh_from_db();planned.refresh_from_db()
        self.assertEqual((current.status,future.status),('retired','retired'))
        self.assertEqual(planned.recipe_id,future.pk)
        self.assertEqual(PR.standard(planned)[self.raw][0],Decimal(20))
        self.assertEqual(PR.current_recipe(self.bulk,day).pk,latest.pk)
        self.assertEqual(PR.create_order(self.maker,self.site,self.bulk,10,'B',planned_date=day).recipe_id,latest.pk)

    def test_new_future_revision_preserves_earlier_windows_and_supersedes_later_lower_version(self):
        current=self.approve(self.draft())
        earlier=self.approve(self.draft(self.today+timedelta(days=5),2))
        later=self.approve(self.draft(self.today+timedelta(days=20),3))
        replacement=self.approve(self.draft(self.today+timedelta(days=10),4))
        later.refresh_from_db();earlier.refresh_from_db();current.refresh_from_db()
        self.assertEqual((current.status,earlier.status,later.status),('approved','approved','retired'))
        for days,expected in [(0,current),(5,earlier),(10,replacement),(25,replacement)]:
            self.assertEqual(PR.current_recipe(self.bulk,self.today+timedelta(days=days)).pk,expected.pk)

    def test_older_future_draft_cannot_roll_back_a_newer_earlier_effective_formula(self):
        old=self.draft(self.today+timedelta(days=20))
        latest=self.approve(self.draft(self.today+timedelta(days=10),2))
        with self.assertRaises(ValidationError):self.approve(old)
        self.assertEqual(PR.current_recipe(self.bulk,self.today+timedelta(days=25)).pk,latest.pk)
        self.assertIsNone(AW.source('recipe',old.pk))

    def test_backdated_older_draft_cannot_displace_current_newer_approval(self):
        old=self.draft(self.today-timedelta(days=30))
        latest=self.approve(self.draft())
        with self.assertRaises(ValidationError):self.approve(old)
        self.assertEqual(PR.current_recipe(self.bulk).pk,latest.pk)

    def test_stale_delegated_approval_leaves_no_use_and_does_not_retire_current(self):
        old=self.draft();latest=self.approve(self.draft(quantity=2))
        admin=User.objects.create_superuser('effectivity-admin','qa@example.com','Fictional-password-816!')
        deputy=User.objects.create_user('effectivity-deputy');deputy.user_permissions.add(Permission.objects.get(codename='view_recipe'))
        now=timezone.now();D.create(admin,self.checker,deputy,'approve_recipe',None,now,now+timedelta(days=1),'Effective formula review')
        with self.assertRaises(ValidationError):PR.approve_recipe(deputy,old.pk)
        self.assertFalse(ApprovalDelegationUse.objects.exists())
        latest.refresh_from_db();self.assertEqual(latest.status,'approved')

    def test_retired_formula_requires_a_new_version_to_become_current_again(self):
        first=self.approve(self.draft(quantity=1));second=self.approve(self.draft(quantity=2))
        with self.assertRaises(ValidationError):self.approve(first)
        copy=self.approve(self.draft(quantity=1))
        self.assertGreater(copy.version,second.version);self.assertEqual(PR.current_recipe(self.bulk).pk,copy.pk)
