"""Opt-in incentive workflow and responsive source-evidence browser QA."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from . import schemes
from .models import SchemeVersion, SchemeEvaluation
from .test_support import seed_business_data
from .test_schemes import approved_scheme, draft_scheme, invoice_fixture, credit_fixture


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in scheme browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class SchemeBrowserTests(StaticLiveServerTestCase):
    def test_screens_slab_form_approval_calculation_and_retry(self):
        from playwright.sync_api import sync_playwright
        from uuid import uuid4
        f=SimpleNamespace(); seed_business_data(f); f.today=timezone.localdate()
        inv=invoice_fixture(f);credit_fixture(f,inv,100,inv.lines.first())
        version=approved_scheme(f)
        result=schemes.calculate(f.maker,version.pk,version.starts_on,version.ends_on,uuid4())
        draft=draft_scheme(f,code='QA-DRAFT')
        submitted=draft_scheme(f,code='QA-PENDING');schemes.submit(f.maker,submitted.pk,'Fictional review request')
        reviewer=Client();reviewer.force_login(f.checker)
        reviewer_cookie=reviewer.cookies[settings.SESSION_COOKIE_NAME].value
        self.client.force_login(f.maker)
        routes={'schemes':reverse('schemes'),'new':reverse('scheme_new'),'draft':reverse('scheme_detail',args=[draft.pk]),
                'edit':reverse('scheme_edit',args=[draft.pk]),'approved':reverse('scheme_detail',args=[version.pk]),
                'calculation':reverse('scheme_evaluation',args=[result.pk])}
        out=Path(settings.BASE_DIR)/'test-results'/'schemes';out.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch();context=browser.new_context()
            context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=self.client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
            page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            for label,width,height in [('small-phone',320,720),('phone',390,844),('tablet',768,1024),('desktop',1440,900),('desktop-collapsed',1470,834)]:
                page.set_viewport_size(dict(width=width,height=height))
                for name,path in routes.items():
                    with self.subTest(size=label,route=name):
                        response=page.goto(self.live_server_url+path)
                        self.assertEqual(response.status,200)
                        page.evaluate('document.fonts.ready')
                        self.assertIn('Poppins',page.evaluate('getComputedStyle(document.body).fontFamily'))
                        if label.endswith('collapsed') and not page.locator('html').evaluate("el=>el.classList.contains('sb-collapsed')"):
                            page.get_by_role('button',name='Collapse sidebar',exact=True).click()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'{name} overflows at {width}px')
                        page.screenshot(path=str(out/f'{name}-{label}.png'),full_page=True,animations='disabled')
            page.goto(self.live_server_url+reverse('scheme_edit',args=[draft.pk]))
            before=int(page.locator('[name=slabs-TOTAL_FORMS]').input_value())
            page.get_by_role('button',name='+ Add slab',exact=True).click()
            self.assertEqual(int(page.locator('[name=slabs-TOTAL_FORMS]').input_value()),before+1)
            page.locator(f'[name=slabs-{before}-minimum_sales]').fill('10000')
            page.locator(f'[name=slabs-{before}-percentage]').fill('5')
            page.get_by_role('button',name='Save draft rules',exact=True).click()
            page.wait_for_url('**/schemes/versions/*/')
            self.assertIn('5.00%',page.locator('main').inner_text())
            review_context=browser.new_context()
            review_context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=reviewer_cookie,url=self.live_server_url)])
            screen=review_context.new_page();screen.on('pageerror',lambda error:errors.append(str(error)))
            screen.goto(self.live_server_url+reverse('scheme_detail',args=[submitted.pk]))
            screen.locator('#review-note').fill('Independent fictional rule review completed')
            screen.get_by_role('button',name='Approve rules',exact=True).click()
            screen.wait_for_load_state('networkidle')
            self.assertIn('Approved',screen.locator('main').inner_text())
            page.goto(self.live_server_url+reverse('scheme_detail',args=[submitted.pk]))
            form=page.locator('form').filter(has=page.get_by_role('button',name='Calculate preview',exact=True))
            payload=form.evaluate('(el)=>Object.fromEntries(new FormData(el))')
            page.get_by_role('button',name='Calculate preview',exact=True).click()
            page.wait_for_url('**/schemes/calculations/*/')
            target=page.url
            retry=context.request.post(self.live_server_url+reverse('scheme_action',args=[submitted.pk,'calculate']),form=payload)
            self.assertEqual(retry.url,target)
            page.get_by_role('button',name='Check current sources',exact=True).click()
            page.wait_for_load_state('networkidle')
            self.assertIn('match this retained calculation',page.locator('body').inner_text())
            self.assertEqual(errors,[])
            browser.close()
        self.assertEqual(SchemeVersion.objects.get(pk=draft.pk).slabs.count(),3)
        self.assertEqual(SchemeVersion.objects.get(pk=submitted.pk).status,'approved')
        self.assertEqual(SchemeEvaluation.objects.filter(version=submitted).count(),1)
