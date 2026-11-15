"""Opt-in requester-to-independent-deputy manual correction at responsive sizes."""
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
from . import gl
from .models import JournalEntry,JournalReversalRequest
from .test_support import seed_business_data


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in manual journal reversal browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class JournalReversalBrowserTests(StaticLiveServerTestCase):
    def test_request_then_independent_delegated_approval_and_balanced_reversal(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_business_data(f);today=timezone.localdate()
        entry=gl.create_voucher(f.maker,today,f.site,'Fictional accrual',[(gl.acct('power'),Decimal(100),0,None,''),(gl.acct('bank_main'),0,Decimal(100),None,'')])
        gl.decide_voucher(f.checker,entry.pk,True,'Independent original review')
        admin=User.objects.create_superuser('reversal-browser-admin','qa@example.com','Fictional-password-816!')
        deputy=User.objects.create_user('reversal-browser-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_journalentry','access_company_finance']))
        now=timezone.now();D.create(admin,f.checker,deputy,'approve_journalentry',None,now,now+timedelta(days=1),'Independent correction review cover')
        sessions={}
        for name,user in [('maker',f.maker),('deputy',deputy)]:
            client=Client();force_login_recent(client,user);sessions[name]=client.cookies[settings.SESSION_COOKIE_NAME].value
        url=self.live_server_url+reverse('journal_detail',args=[entry.pk]);errors=[]
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=sessions['maker'],url=self.live_server_url)])
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900));self.assertEqual(page.goto(url).status,200)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.get_by_text('Request reversal of this voucher',exact=True).click()
            page.locator('#rv-d').fill(str(today));page.locator('#rv-r').fill('Duplicate accrual entered in error')
            page.get_by_role('button',name='Request reversal',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Review pending',page.locator('main').inner_text())
            self.assertEqual(page.get_by_role('button',name='Approve reversal',exact=True).count(),0)
            browser.close()
        request=JournalReversalRequest.objects.get(entry=entry)
        self.assertEqual(JournalEntry.objects.count(),1)
        H.assign_approval(admin,'journal_reversal',request.pk,deputy,'Named independent correction reviewer',uuid4())
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=sessions['deputy'],url=self.live_server_url)])
            page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900))
                for route in [self.live_server_url+reverse('approval_inbox'),url]:
                    self.assertEqual(page.goto(route).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.locator(f'#reversal-review-{request.pk}').fill('Source and correction independently reconciled')
            page.get_by_role('button',name='Approve reversal',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Posted reversal',page.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('approval_inbox'))
            self.assertIn('No pending assigned approvals available',page.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        request.refresh_from_db();self.assertEqual(request.reviewed_by_id,deputy.pk)
        self.assertEqual(request.reversal.created_by_id,f.maker.pk);self.assertEqual(request.reversal.approved_by_id,deputy.pk)
        self.assertEqual(JournalEntry.objects.filter(reversal_of=entry).count(),1)
        self.assertEqual(ApprovalDelegationUse.objects.filter(actor=deputy).count(),1)
