"""Opt-in delegated BOM/weight approval layouts and actual decisions."""
import os,importlib.util
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import skipUnless
from uuid import uuid4
from django.conf import settings
from django.contrib.auth.models import User,Permission
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts import delegation as D,work_handoff as H
from accounts.models import ApprovalDelegationUse
from accounts.test_support import force_login_recent
from . import production as PR,test_logistics as fixture
from .models import Product,SiteAccess


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in operational approval browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class OperationalApprovalBrowserTests(StaticLiveServerTestCase):
    def test_assigned_bom_and_weight_review_across_responsive_layouts(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();fixture.LogisticsTests.setUpTestData.__func__(f);fixture.LogisticsTests.setUp(f)
        dispatch=fixture.LogisticsTests.run_to_weighed(f,qty=400,gross=29600)
        raw=Product.objects.create(code='OPS-BROWSER-RAW',name='Fictional recipe material',category='Raw material',unit='MT')
        recipe=PR.save_recipe(f.admin,f.cement,[(raw,Decimal('0.05'),Decimal(2))])
        principal=User.objects.create_superuser('ops-browser-principal','qa@example.com','Fictional-password-816!')
        deputy=User.objects.create_user('ops-browser-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_recipe','view_dispatch']))
        SiteAccess.objects.create(user=deputy,site=f.site)
        now=timezone.now()
        for permission,site in [('approve_recipe',None),('approve_weight_exception',f.site)]:
            D.create(f.admin,principal,deputy,permission,site,now,now+timedelta(days=1),'Operational approval cover')
        for kind,pk in [('recipe',recipe.pk),('weight_exception',dispatch.pk)]:
            H.assign_approval(f.admin,kind,pk,deputy,'Named independent operational checker',uuid4())
        client=Client();force_login_recent(client,deputy);cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900))
                for route in [reverse('approval_inbox'),reverse('recipes'),reverse('dispatch_detail',args=[dispatch.pk])]:
                    self.assertEqual(page.goto(self.live_server_url+route).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.locator('#ap-note').fill('Independent retained weighing and tolerance review')
            page.get_by_role('button',name='Approve exception',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Independent retained weighing and tolerance review',page.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('recipes'))
            page.get_by_role('button',name='Approve',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Approved',page.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('approval_inbox'))
            self.assertIn('No pending assigned approvals available',page.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        dispatch.refresh_from_db();recipe.refresh_from_db()
        self.assertEqual(dispatch.exception_approved_by_id,deputy.pk);self.assertEqual(recipe.approved_by_id,deputy.pk)
        self.assertEqual(ApprovalDelegationUse.objects.filter(actor=deputy).count(),2)
