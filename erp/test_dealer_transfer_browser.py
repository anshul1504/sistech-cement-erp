"""Opt-in reviewed dated transfer across phone/desktop layouts."""
import os,importlib.util
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from accounts.test_support import force_login_recent
from .models import DealerOwnershipTransfer,SiteAccess
from .test_beats import seed_beats,approved_plan


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in ownership transfer browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class DealerTransferBrowserTests(StaticLiveServerTestCase):
    def test_preview_confirmation_receipt_and_retained_routes(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_beats(f);on=f.today+timedelta(days=1);plan,stop=approved_plan(f,on)
        recipient=User.objects.create_user('browser-transfer-owner');recipient.user_permissions.set(f.rep.user_permissions.all())
        SiteAccess.objects.create(user=recipient,site=f.site)
        client=Client();force_login_recent(client,f.maker);cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            url=self.live_server_url+reverse('dealer_transfer_new',args=[f.assignment.pk])
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900));self.assertEqual(page.goto(url).status,200)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.locator('[name=recipient]').select_option(str(recipient.pk));page.locator('[name=effective_on]').fill(str(on))
            page.locator('[name=reason]').fill('Reviewed permanent future dealer handover')
            page.get_by_role('button',name='Preview transfer',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('1 future stops',page.locator('main').inner_text())
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900));self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.get_by_role('button',name='Confirm reviewed transfer',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Dealer ownership transferred',page.locator('h1').inner_text())
            receipt_url=page.url
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900));page.goto(receipt_url)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.goto(self.live_server_url+reverse('beat_detail',args=[plan.pk]))
            self.assertIn('Reassigned to replacement stop',page.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        self.assertEqual(DealerOwnershipTransfer.objects.count(),1)
        stop.refresh_from_db();self.assertEqual(stop.status,'reassigned');self.assertEqual(stop.replacement_stop.plan.owner_id,recipient.pk)
