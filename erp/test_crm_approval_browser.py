"""Opt-in assigned/delegated KYC browser decision with retained actual reviewer."""
import os,importlib.util
from datetime import timedelta
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
from .models import SiteAccess
from .test_crm import seed_crm,submitted


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in onboarding approval browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class CRMApprovalBrowserTests(StaticLiveServerTestCase):
    def test_assigned_deputy_review_and_mobile_detail(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_crm(f);app=submitted(f)
        admin=User.objects.create_superuser('approval-browser-admin','qa@example.com','Fictional-password-889!')
        deputy=User.objects.create_user('approval-browser-deputy')
        deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_dealerapplication','view_team_crm','view_crmlead','view_dealerdocument']))
        SiteAccess.objects.create(user=deputy,site=f.site)
        now=timezone.now();grant=D.create(admin,f.checker,deputy,'review_dealerkyc',f.site,now,now+timedelta(days=1),'Fictional KYC cover')
        H.assign_approval(admin,'dealer_kyc',app.pk,deputy,'Accountable delegated KYC reviewer',uuid4())
        client=Client();force_login_recent(client,deputy);cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900))
                for route in [reverse('approval_inbox'),reverse('crm_application_detail',args=[app.pk])]:
                    self.assertEqual(page.goto(self.live_server_url+route).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.locator('#review-note').fill('Independent delegated KYC evidence review')
            page.get_by_role('button',name='Approve KYC',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Independent delegated KYC evidence review',page.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('approval_inbox'))
            self.assertIn('No pending assigned approvals available',page.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        app.refresh_from_db();self.assertEqual(app.kyc_by_id,deputy.pk);self.assertEqual(app.status,'kyc')
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=deputy).exists())
