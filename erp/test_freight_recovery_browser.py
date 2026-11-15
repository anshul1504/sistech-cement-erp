"""Optional real-browser review of a late-POD recovery across viewport sizes."""
import os,importlib.util
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
from .models import FreightRecoveryAdjustment

@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in freight recovery browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class RecoveryBrowserTests(StaticLiveServerTestCase):
    def test_proposal_and_independent_review(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();fixture.FreightTests.setUpTestData.__func__(f)
        trip=fixture.FreightTests.trip(f,f.truck,f.driver,100)
        bill=F.create_bill(f.admin,f.trans,'REC-BROWSER',timezone.localdate(),{trip:dict(claimed=trip.freight_amount,days=0,extra=0,reason='')})
        F.decide(f.checker,bill.pk,True,'Contract verified')
        L.deliver(f.admin,trip.pk,'Fictional receiver',shortage=4,notes='POD shortage')
        clients=[]
        for user in [f.admin,f.checker]:
            client=Client();force_login_recent(client,user);clients.append(client.cookies[settings.SESSION_COOKIE_NAME].value)
        with sync_playwright() as p:
            browser=p.chromium.launch();errors=[]
            for index,cookie in enumerate(clients):
                context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                for width in [320,390,768,1440]:
                    page.set_viewport_size(dict(width=width,height=900))
                    self.assertEqual(page.goto(self.live_server_url+reverse('freight_bill_detail',args=[bill.pk])).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                if index==0:
                    page.locator('#recovery-reason').fill('POD shortage confirmed')
                    page.get_by_role('button',name='Request recovery adjustment',exact=True).click()
                else:
                    page.locator('input[id^="recovery-review-"]').fill('Independent POD confirmation')
                    page.get_by_role('button',name='Approve recovery',exact=True).click()
                page.wait_for_load_state('networkidle')
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                context.close()
            self.assertEqual(errors,[]);browser.close()
        request=FreightRecoveryAdjustment.objects.get(bill=bill)
        self.assertEqual(request.status,'approved');self.assertEqual(request.reviewed_by_id,f.checker.pk)
        self.assertEqual(R.effective_payable(bill),7400)
