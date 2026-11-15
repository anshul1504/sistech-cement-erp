"""Opt-in scoped delegated CAPA verification and complaint review on mobile/desktop."""
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
from . import complaints as CM
from .models import SiteAccess
from .test_support import seed_business_data
from .test_schemes import invoice_fixture


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in complaint/CAPA approval browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class ComplaintApprovalBrowserTests(StaticLiveServerTestCase):
    def test_assigned_capa_verification_and_delegated_case_review(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_business_data(f);f.today=timezone.localdate();today=f.today;inv=invoice_fixture(f,[(f.bag,Decimal(100))])
        principal=User.objects.create_superuser('capa-browser-principal','qa@example.com','Fictional-password-816!')
        deputy=User.objects.create_user('capa-browser-deputy');deputy.user_permissions.set(Permission.objects.filter(codename__in=['view_complaint','view_capaaction']))
        SiteAccess.objects.create(user=deputy,site=f.site)
        case=CM.log(f.maker,f.customer,'quality','Fictional reviewed quality case',inv,quantity=10)
        CM.investigate(f.checker,case.pk,'Independent reviewed investigation')
        capa=CM.add_capa(f.maker,case,'Fictional corrective action','Root cause','Corrective action',f.checker,today+timedelta(days=2))
        CM.implement(f.maker,capa.pk,'Documented implementation')
        now=timezone.now()
        for permission in ['verify_capaaction','decide_complaint']:
            D.create(principal,principal,deputy,permission,f.site,now,now+timedelta(days=1),'Quality review cover')
        H.assign_approval(principal,'capa_verification',capa.pk,deputy,'Independent effectiveness checker',uuid4())
        client=Client();force_login_recent(client,deputy);cookie=client.cookies[settings.SESSION_COOKIE_NAME].value
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context();context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            for width in [320,390,768,1440]:
                page.set_viewport_size(dict(width=width,height=900))
                for route in [reverse('approval_inbox'),reverse('complaint_detail',args=[case.pk]),reverse('capa_detail',args=[capa.pk])]:
                    self.assertEqual(page.goto(self.live_server_url+route).status,200)
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
            page.locator('#effectiveness-note').fill('Independent evidence shows no repeat issue')
            page.get_by_role('button',name='Verify effective',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Verified effective',page.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('approval_inbox'))
            self.assertIn('No pending assigned approvals available',page.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('complaint_detail',args=[case.pk]))
            page.locator('#cd-d').select_option('reject');page.locator('#cd-q').fill('0');page.locator('#cd-r').select_option('none')
            page.locator('#cd-n').fill('Independent complaint decision after investigation review')
            page.get_by_role('button',name='Record decision',exact=True).click();page.wait_for_load_state('networkidle')
            page.get_by_role('button',name='Close',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Closed',page.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        capa.refresh_from_db();self.assertEqual(capa.status,'effective');self.assertEqual(capa.verified_by_id,deputy.pk)
        self.assertTrue(ApprovalDelegationUse.objects.filter(actor=deputy,delegation__permission='verify_capaaction').exists())
        case.refresh_from_db();self.assertEqual(case.closed_by_id,deputy.pk);self.assertEqual(case.status,'closed')
        self.assertEqual(ApprovalDelegationUse.objects.filter(actor=deputy).count(),3)
