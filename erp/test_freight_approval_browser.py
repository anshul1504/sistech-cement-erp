"""Opt-in delegated freight rejection retaining claim evidence on narrow screens."""
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
from . import freight as F
from . import test_freight as freight_fixture


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in freight approval browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class FreightApprovalBrowserTests(StaticLiveServerTestCase):
    def test_assigned_delegated_rejection_preserves_trip_evidence(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();freight_fixture.FreightTests.setUpTestData.__func__(f)
        dispatch=freight_fixture.FreightTests.trip(f,f.truck,f.driver,100)
        bill=F.create_bill(f.admin,f.trans,'FREIGHT-BROWSER-QA',timezone.localdate(),{
            dispatch:dict(claimed=dispatch.freight_amount+50,days=1,extra=Decimal('2000'),reason='Fictional detention')})
        deputy=User.objects.create_user('freight-browser-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_freightbill','access_company_finance']))
        now=timezone.now();grant=D.create(f.admin,f.checker,deputy,'approve_freightbill',None,now,now+timedelta(days=1),'Freight review cover')
        H.assign_approval(f.admin,'freight_bill',bill.pk,deputy,'Independent accountable freight checker',uuid4())
        client=Client();force_login_recent(client,deputy);cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900))
                for route in [reverse('approval_inbox'),reverse('freight_bill_detail',args=[bill.pk])]:
                    self.assertEqual(page.goto(self.live_server_url+route).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.locator('#fb-note').fill('Detention evidence does not match transporter claim')
            page.get_by_role('button',name='Reject',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('retained decision evidence',page.locator('main').inner_text())
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900));page.reload()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.goto(self.live_server_url+reverse('approval_inbox'))
            self.assertIn('No pending assigned approvals available',page.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        bill.refresh_from_db();self.assertEqual(bill.checked_by_id,deputy.pk);self.assertEqual(bill.status,'rejected')
        self.assertEqual(bill.review_snapshot['trips'][0]['dispatch_id'],dispatch.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(delegation=grant,actor=deputy).exists())
        self.assertTrue(F.billable_trips(f.trans).filter(pk=dispatch.pk).exists())
