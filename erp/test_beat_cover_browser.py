"""Opt-in responsive proposal, independent review and covered-route execution."""
import importlib.util
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.test_support import force_login_recent
from .models import BeatExecutionCover, BeatStop, SiteAccess
from .test_beats import seed_beats, approved_plan


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in execution-cover browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class CoverBrowserTests(StaticLiveServerTestCase):
    def test_proposal_review_execution_and_responsive_layout(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_beats(f);plan,stop=approved_plan(f)
        recipient=User.objects.create_user('browser-cover')
        recipient.user_permissions.set(f.rep.user_permissions.all());SiteAccess.objects.create(user=recipient,site=f.site)
        sessions={}
        for name,user in [('manager',f.maker),('checker',f.checker),('executor',recipient)]:
            client=Client();force_login_recent(client,user);sessions[name]=client.cookies[settings.SESSION_COOKIE_NAME].value
        end=(timezone.localtime()+timedelta(days=1)).strftime('%Y-%m-%dT%H:%M')
        with sync_playwright() as p:
            browser=p.chromium.launch();pages={};errors=[]
            for name,cookie in sessions.items():
                context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                pages[name]=context.new_page();pages[name].on('pageerror',lambda error:errors.append(str(error)))
            manager=pages['manager'];checker=pages['checker'];executor=pages['executor']
            for width in [320,390,768,1440]:
                manager.set_viewport_size(dict(width=width,height=900))
                response=manager.goto(self.live_server_url+reverse('beat_cover_new',args=[plan.pk]));self.assertEqual(response.status,200)
                self.assertTrue(manager.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            manager.locator('[name=recipient]').select_option(str(recipient.pk))
            manager.locator('[name=ends_at]').fill(end);manager.locator('[name=reason]').fill('Temporary route coverage')
            manager.get_by_role('button',name='Save',exact=True).click();manager.wait_for_load_state('networkidle')
            cover_url=manager.url
            checker.goto(cover_url);checker.locator('#review-note').fill('Independent review of cover and pending visits')
            checker.get_by_role('button',name='Approve cover',exact=True).click();checker.wait_for_load_state('networkidle')
            for width in [320,390,768,1440]:
                executor.set_viewport_size(dict(width=width,height=900))
                for url in [cover_url,self.live_server_url+reverse('beat_detail',args=[plan.pk])]:
                    self.assertEqual(executor.goto(url).status,200)
                    self.assertTrue(executor.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            executor.get_by_role('link',name='Record visit',exact=True).click()
            executor.locator('[name=outcome]').select_option('visited');executor.locator('[name=note]').fill('Visit executed by cover recipient')
            executor.get_by_role('button',name='Save',exact=True).click();executor.wait_for_load_state('networkidle')
            self.assertIn('Visit executed by cover recipient',executor.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        self.assertEqual(BeatExecutionCover.objects.get(plan=plan).status,'approved')
        stop.refresh_from_db();plan.refresh_from_db()
        self.assertEqual(stop.recorded_by_id,recipient.pk);self.assertEqual(plan.owner_id,f.rep.pk)
