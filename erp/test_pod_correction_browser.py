"""Opt-in responsive independent POD correction and restored freight liability."""
import importlib.util,os,uuid
from types import SimpleNamespace
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from django.utils import timezone
from accounts.test_support import force_login_recent
from . import test_freight as fixture
from . import freight as F,logistics as L,freight_recovery as R
from .models import PODCorrection

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in POD correction browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class PODCorrectionBrowserTests(StaticLiveServerTestCase):
    def test_review_and_signed_freight_correction(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();fixture.FreightTests.setUpTestData.__func__(f)
        trip=fixture.FreightTests.trip(f,f.truck,f.driver,100)
        bill=F.create_bill(f.admin,f.trans,'POD-COR-BROWSER',timezone.localdate(),{trip:dict(claimed=trip.freight_amount,days=0,extra=0,reason='')})
        F.decide(f.checker,bill.pk,True,'Contract verified')
        L.deliver(f.admin,trip.pk,'Fictional receiver',shortage=4,notes='Original shortage')
        req=R.propose(f.admin,bill.pk,timezone.localdate(),'Late shortage',uuid.uuid4());R.decide(f.checker,req.pk,True,'Verified')
        cookies=[]
        for user in [f.admin,f.checker]:
            client=Client();force_login_recent(client,user);cookies.append(client.cookies[settings.SESSION_COOKIE_NAME].value)
        with sync_playwright() as p:
            browser=p.chromium.launch();errors=[]
            for index,cookie in enumerate(cookies):
                context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                for width in [320,390,768,1440]:
                    page.set_viewport_size(dict(width=width,height=900))
                    self.assertEqual(page.goto(self.live_server_url+reverse('dispatch_detail',args=[trip.pk])).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                if index==0:
                    page.locator('#pc-short').fill('2');page.locator('#pc-notes').fill('Receiver recount confirmed')
                    page.locator('#pc-reason').fill('Two bags located at receiver site')
                    page.get_by_role('button',name='Request POD correction',exact=True).click()
                else:
                    page.locator('input[id^="pc-review-"]').fill('Independent recount verified')
                    page.get_by_role('button',name='Approve POD correction',exact=True).click()
                page.wait_for_load_state('networkidle')
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                context.close()
            self.assertEqual(errors,[]);browser.close()
        correction=PODCorrection.objects.get();self.assertEqual(correction.status,'approved')
        restore=R.propose(f.admin,bill.pk,timezone.localdate(),'Reviewed shortage reduction',uuid.uuid4())
        self.assertEqual(restore.amount,-800)
        R.decide(f.checker,restore.pk,True,'Restore reviewed liability')
        self.assertEqual(R.effective_payable(bill),8200)
