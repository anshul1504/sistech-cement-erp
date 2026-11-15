"""Opt-in bank-free offset proposal, review, settlement and reversal on responsive screens."""
import importlib.util,os,re
from types import SimpleNamespace,MethodType
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from accounts.test_support import force_login_recent
from . import test_non_cash_offsets as fixture
from . import treasury as T
from .models import SupplierPayment

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in non-cash offset browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class OffsetBrowserTests(StaticLiveServerTestCase):
    def test_non_cash_offset_review_settle_and_reverse(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();fixture.OffsetTests.setUpTestData.__func__(f)
        for name in ['trip','credit','setup_offset']:setattr(f,name,MethodType(getattr(fixture.OffsetTests,name),f))
        reserved,_,_,picks=f.setup_offset();T.decide(f.checker,reserved.pk,False,'Create from browser')
        cookies=[]
        for user in [f.admin,f.checker]:
            client=Client();force_login_recent(client,user);cookies.append(client.cookies[settings.SESSION_COOKIE_NAME].value)
        url=''
        with sync_playwright() as p:
            browser=p.chromium.launch();errors=[]
            for index,cookie in enumerate(cookies):
                context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                if index==0:
                    for width in [320,390,768,1440]:
                        page.set_viewport_size(dict(width=width,height=900));self.assertEqual(page.goto(self.live_server_url+reverse('payment_new',args=[f.trans.pk])).status,200)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    page.select_option('#py-m','offset');self.assertTrue(page.locator('#py-b').is_disabled())
                    page.locator('#py-n').fill('Fictional vendor agreement to offset credit')
                    for (kind,pk),amount in picks.items():
                        page.locator(f'[name="pick_{kind}_{pk}"]').check();page.locator(f'[name="amt_{kind}_{pk}"]').fill(str(amount))
                    page.get_by_role('button',name='Propose offset',exact=True).click();page.wait_for_load_state('networkidle');url=page.url
                    self.assertIn('Offset awaiting approval',page.locator('main').inner_text())
                else:
                    page.goto(url);page.locator('#pa-n').fill('Sources and agreement independently checked')
                    page.get_by_role('button',name='Approve',exact=True).click();page.wait_for_load_state('networkidle')
                    self.assertEqual(page.get_by_role('button',name='Mark paid',exact=True).count(),0)
                    page.locator('#offset-note').fill('Equal credits and bills confirmed')
                    page.get_by_role('button',name='Settle without cash',exact=True).click();page.wait_for_load_state('networkidle')
                    self.assertIn('Settled without cash',page.locator('main').inner_text())
                    for width in [320,390,768,1440]:
                        page.set_viewport_size(dict(width=width,height=900));page.reload();self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    page.locator('#offset-reason').fill('Vendor agreement subsequently withdrawn')
                    page.get_by_role('button',name='Reverse settlement',exact=True).click();page.wait_for_load_state('networkidle')
                    self.assertIn('Settlement reversed',page.locator('main').inner_text())
                context.close()
            self.assertEqual(errors,[]);browser.close()
        pk=int(re.search(r'/(\d+)/?$',url).group(1));pay=SupplierPayment.objects.get(pk=pk)
        self.assertEqual(pay.status,'failed');self.assertIsNone(pay.bank_id);self.assertFalse(pay.reference)
        self.assertEqual(pay.returned_by_id,f.checker.pk);self.assertEqual(pay.settlement_note,'Equal credits and bills confirmed')
