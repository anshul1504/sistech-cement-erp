"""Opt-in delegated recognition publication and usage verification on narrow screens."""
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
from . import recognition as RC
from .models import RecognitionProgram,RecognitionLevel,Influencer
from .test_support import seed_business_data
from .test_schemes import invoice_fixture


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in recognition approval browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class RecognitionApprovalBrowserTests(StaticLiveServerTestCase):
    def test_delegated_usage_and_publication_responsive_flow(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_business_data(f);f.today=timezone.localdate();invoice_fixture(f,[(f.bag,Decimal(100))])
        admin=User.objects.create_superuser('star-browser-admin','qa@example.com','Fictional-password-816!')
        deputy=User.objects.create_user('star-browser-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_recognitionprogram','view_influencerusage']))
        program=RecognitionProgram.objects.create(name='Fictional star recognition',audience='dealer',starts_on=f.today-timedelta(days=2),ends_on=f.today,created_by=f.maker)
        RecognitionLevel.objects.create(program=program,level='national',winners=1,minimum=0,reward='Fictional trophy');RC.rank(f.maker,program.pk)
        influencer=Influencer.objects.create(code='STAR-BROWSER',name='Fictional mason',kind='Mason')
        usage=RC.record_usage(f.maker,influencer,f.customer,f.bag,10,f.today,'STAR-BROWSER-BILL')
        now=timezone.now()
        for permission in ['publish_recognitionprogram','verify_influencerusage']:
            D.create(admin,f.checker,deputy,permission,None,now,now+timedelta(days=1),'Recognition cover')
        for kind,pk in [('recognition_publication',program.pk),('recognition_usage',usage.pk)]:
            H.assign_approval(admin,kind,pk,deputy,'Accountable independent recognition reviewer',uuid4())
        client=Client();force_login_recent(client,deputy);cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900))
                for route in [reverse('approval_inbox'),reverse('recognition_detail',args=[program.pk]),reverse('influencer_usage')]:
                    self.assertEqual(page.goto(self.live_server_url+route).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.get_by_role('button',name='Verify',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertNotIn('STAR-BROWSER-BILL',page.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('recognition_detail',args=[program.pk]))
            page.get_by_role('button',name='Publish',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Winners published',page.locator('main').inner_text())
            self.assertEqual(page.get_by_role('button',name='Mark given',exact=True).count(),0)
            page.goto(self.live_server_url+reverse('approval_inbox'))
            self.assertIn('No pending assigned approvals available',page.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        usage.refresh_from_db();program.refresh_from_db()
        self.assertEqual(usage.verified_by_id,deputy.pk);self.assertEqual(program.published_by_id,deputy.pk)
        self.assertEqual(ApprovalDelegationUse.objects.filter(actor=deputy).count(),2)
