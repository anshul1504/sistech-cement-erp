"""Optional responsive incoming vendor refund review, receipt and reversal."""
import os,importlib.util,re
from types import SimpleNamespace,MethodType
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from accounts.test_support import force_login_recent
from . import test_vendor_refunds as fixture
from . import gl,treasury as T
from .models import SupplierPayment

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in vendor refund browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class VendorRefundBrowserTests(StaticLiveServerTestCase):
    def test_incoming_refund_proposal_review_receipt_reversal(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();fixture.VendorRefundTests.setUpTestData.__func__(f)
        for name in ['trip','credit']:setattr(f,name,MethodType(getattr(fixture.VendorRefundTests,name),f))
        bill,_,_=f.credit();bank_id=gl.acct('bank_main').pk
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
                    page.select_option('#py-m','refund');page.select_option('#py-b',str(bank_id))
                    page.locator('#py-n').fill('Vendor agreed to refund four missing bags')
                    page.locator(f'[name="pick_frb_{bill.pk}"]').check();page.locator(f'[name="amt_frb_{bill.pk}"]').fill('1600')
                    page.get_by_role('button',name='Propose refund',exact=True).click();page.wait_for_load_state('networkidle');url=page.url
                    self.assertIn('Refund awaiting approval',page.locator('main').inner_text())
                else:
                    page.goto(url);page.locator('#pa-n').fill('Vendor credit and bank checked')
                    page.get_by_role('button',name='Approve',exact=True).click();page.wait_for_load_state('networkidle')
                    page.locator('#refund-reference').fill('BROWSER-INCOMING-REFUND');page.locator('#refund-note').fill('Incoming bank credit verified')
                    page.get_by_role('button',name='Record refund received',exact=True).click();page.wait_for_load_state('networkidle')
                    self.assertIn('Refund received',page.locator('main').inner_text())
                    self.assertEqual(page.get_by_role('button',name='Mark paid',exact=True).count(),0)
                    for width in [320,390,768,1440]:
                        page.set_viewport_size(dict(width=width,height=900));page.reload();self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                    page.locator('#refund-reason').fill('Bank subsequently reversed the credit')
                    page.get_by_role('button',name='Reverse refund receipt',exact=True).click();page.wait_for_load_state('networkidle')
                    self.assertIn('Refund reversed',page.locator('main').inner_text())
                context.close()
            self.assertEqual(errors,[]);browser.close()
        pay=SupplierPayment.objects.get(pk=int(re.search(r'/(\d+)/?$',url).group(1)))
        self.assertEqual(pay.status,'failed');self.assertEqual(pay.amount,-1600);self.assertEqual(pay.returned_by_id,f.checker.pk)
        self.assertEqual(T.open_items(f.trans)[0]['amount'],-1600)
