"""Optional responsive reviewed duplicate-lead consolidation using isolated prospects."""
import os,importlib.util
from types import SimpleNamespace
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from accounts.test_support import force_login_recent
from . import test_crm as fixture
from .models import CRMLeadMerge

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in CRM merge browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class CRMMergeBrowserTests(StaticLiveServerTestCase):
    def test_preview_independent_review_and_retained_alias(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();fixture.seed_crm(f)
        source=fixture.new_lead(f,mobile='9876543210');target=fixture.new_lead(f,mobile='9876543211')
        cookies=[]
        for user in [f.maker,f.checker]:
            client=Client();force_login_recent(client,user);cookies.append(client.cookies[settings.SESSION_COOKIE_NAME].value)
        with sync_playwright() as p:
            browser=p.chromium.launch();errors=[]
            for index,cookie in enumerate(cookies):
                context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                if index==0:
                    page.goto(self.live_server_url+reverse('crm_lead_merge',args=[source.pk]));page.select_option('#merge-target',str(target.pk))
                    page.get_by_role('button',name='Preview merge',exact=True).click();page.wait_for_load_state('networkidle')
                    for width in [320,390,768,1440]:
                        page.set_viewport_size(dict(width=width,height=900));page.reload()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    page.locator('#merge-reason').fill('Both contacts confirmed the same fictional business identity')
                    page.get_by_role('button',name='Request independent merge review',exact=True).click();page.wait_for_load_state('networkidle')
                else:
                    page.goto(self.live_server_url+reverse('crm_lead_detail',args=[source.pk]))
                    page.locator('textarea[id^="merge-note-"]').fill('Independent callback confirmed identity')
                    page.get_by_role('button',name='Approve merge',exact=True).click();page.wait_for_load_state('networkidle')
                    self.assertIn('Retained merged duplicate',page.locator('main').inner_text())
                    self.assertEqual(page.get_by_role('link',name='Edit lead',exact=True).count(),0)
                    for width in [320,390,768,1440]:
                        page.set_viewport_size(dict(width=width,height=900));page.reload()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                context.close()
            self.assertEqual(errors,[]);browser.close()
        source.refresh_from_db();self.assertEqual(source.merged_into_id,target.pk)
        self.assertEqual(CRMLeadMerge.objects.get().reviewed_by_id,f.checker.pk)
