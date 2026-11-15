"""Opt-in CRM browser checks; all customer records and files are fictional."""
from accounts.test_support import force_login_recent
import importlib.util
import os
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from . import crm
from .test_crm import seed_crm, new_lead, submitted, activated


@skipUnless(os.environ.get('RUN_BROWSER_TESTS') == '1' and importlib.util.find_spec('playwright'), 'Opt-in CRM browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class CRMBrowserTests(StaticLiveServerTestCase):
    def test_crm_screens_and_form_at_multiple_sizes(self):
        from playwright.sync_api import sync_playwright
        f = SimpleNamespace(); seed_crm(f)
        lead = new_lead(f)
        app = activated(f, submitted(f, lead))
        draft = crm.start_application(f.maker, lead.pk)
        pending = submitted(f, new_lead(f, mobile='8765432109'))
        crm.save_target(f.maker, dict(site=f.site, owner=f.maker, month=f.today, sales_amount=Decimal(500000), new_dealers=5, note='Fictional QA target'))
        review = app.reviews.get(action='Activated')
        routes = {name: reverse(name) for name in ['crm_leads', 'crm_lead_new', 'crm_applications', 'crm_performance', 'crm_policy']}
        routes.update({key:reverse(name,args=args) for key,name,args in [
            ('lead','crm_lead_detail',[lead.pk]),('lead-edit','crm_lead_edit',[pending.lead_id]),('approved','crm_application_detail',[app.pk]),
            ('draft','crm_application_detail',[draft.pk]),('pending','crm_application_detail',[pending.pk]),('profile','crm_application_edit',[draft.pk]),
            ('review','crm_review_detail',[review.pk]),('performance-detail','crm_performance_detail',[f.site.pk,f.maker.pk]),
            ('partner','partner_detail',[app.partner_id]),('partner-edit','partners_edit',[app.partner_id])
        ]})
        reviewer_sessions = {}
        for key, actor in [('kyc', f.checker), ('credit', f.finance), ('final', f.final)]:
            session_client = Client()
            force_login_recent(session_client, actor)
            reviewer_sessions[key] = session_client.cookies[settings.SESSION_COOKIE_NAME].value
        force_login_recent(self.client, f.maker)
        out=Path(settings.BASE_DIR)/'test-results'/'crm';out.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch()
            context=browser.new_context()
            context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=self.client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
            page=context.new_page(); errors=[]
            page.on('pageerror', lambda error:errors.append(str(error)))
            for size,width,height in [('small-phone',320,720),('phone',390,844),('tablet',768,1024),('desktop',1440,900),('desktop-collapsed',1470,834)]:
                page.set_viewport_size(dict(width=width,height=height))
                for name,path in routes.items():
                    with self.subTest(size=size,route=name):
                        response=page.goto(self.live_server_url+path)
                        self.assertEqual(response.status,200)
                        page.evaluate('document.fonts.ready')
                        self.assertIn('Poppins',page.evaluate('getComputedStyle(document.body).fontFamily'))
                        if size.endswith('collapsed') and not page.locator('html').evaluate("el=>el.classList.contains('sb-collapsed')"):
                            page.get_by_role('button',name='Collapse sidebar',exact=True).click()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'{name} overflows at {width}px')
                        if name == 'crm_policy':
                            checks = page.locator('.crm-policy input[type=checkbox]')
                            self.assertEqual(checks.count(), 9)
                            self.assertTrue(checks.evaluate_all('(els) => els.every(el => {const r=el.getBoundingClientRect(); return r.width>=16 && r.width<=22 && r.height>=16 && r.height<=22;})'))
                            self.assertTrue(page.locator('[name=required_documents][value=pan_card]').is_checked())
                        page.screenshot(path=str(out/f'{name}-{size}.png'),full_page=True,animations='disabled')
            page.goto(self.live_server_url+reverse('crm_policy'))
            page.get_by_label('Require GSTIN for all new submissions', exact=False).check()
            page.get_by_role('button',name='Save policy',exact=True).click()
            page.wait_for_load_state('networkidle')
            self.assertTrue(page.locator('[name=require_gstin]').is_checked())
            page.locator('label[for=id_required_documents_1]').click()
            self.assertFalse(page.locator('[name=required_documents][value=pan_card]').is_checked())
            page.locator('label[for=id_required_documents_1]').click()
            self.assertTrue(page.locator('[name=required_documents][value=pan_card]').is_checked())
            page.goto(self.live_server_url+reverse('crm_lead_new'))
            page.locator('[name=site]').select_option(str(f.site.pk))
            page.locator('[name=owner]').select_option(str(f.maker.pk))
            page.locator('[name=business_name]').fill('Browser fictional prospect')
            page.locator('[name=mobile]').fill('+91 76543 21098')
            page.locator('[name=kind]').select_option('Dealer')
            page.locator('[name=source]').select_option('visit')
            page.locator('[name=estimated_value]').fill('125000')
            page.get_by_role('button',name='Save lead',exact=True).click()
            page.wait_for_url('**/crm/leads/*/')
            self.assertIn('Browser fictional prospect',page.locator('h1').inner_text())
            for lane,button in [('kyc','Approve KYC'),('credit','Approve credit & terms'),('final','Approve & apply to customer')]:
                reviewer = browser.new_context()
                reviewer.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=reviewer_sessions[lane],url=self.live_server_url)])
                screen=reviewer.new_page()
                screen.on('pageerror',lambda error:errors.append(str(error)))
                screen.goto(self.live_server_url+reverse('crm_application_detail',args=[pending.pk]))
                screen.locator('#review-note').fill('Fictional browser evidence checked for '+lane)
                if lane=='credit':
                    screen.locator('#approved-credit').fill('60000')
                    screen.locator('#approved-days').fill('14')
                screen.get_by_role('button',name=button,exact=True).click()
                screen.wait_for_load_state('networkidle')
                reviewer.close()
            self.assertEqual(errors,[])
            browser.close()
        pending.refresh_from_db()
        self.assertEqual(pending.status,'approved')
        self.assertEqual(pending.partner.credit_limit,Decimal(60000))
        self.assertEqual(pending.partner.payment_terms_days,14)
