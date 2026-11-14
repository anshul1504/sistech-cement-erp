"""Opt-in RMA layouts and real independent inspection/credit browser flow."""
import importlib.util
import os
from decimal import Decimal as D
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from . import returns as R
from .models import CreditNote, ReturnRequest, StockBalance
from .test_returns import seed_returns, requested


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in return browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class ReturnBrowserTests(StaticLiveServerTestCase):
    def test_return_screens_and_independent_approval_inspection_credit(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_returns(f)
        pending=requested(f,4)
        hold=requested(f,4);R.decide(f.checker,hold.pk,True,'QA approval')
        R.inspect(f.inspector,hold.pk,{hold.lines.get().pk:(D(4),D(3),D(1),'')})
        transit=requested(f,2);R.decide(f.checker,transit.pk,True,'QA approval')
        R.pickup(f.inspector,transit.pk,f.today,'MP04GA1234','QA-TRANSIT')
        sessions={}
        for name,user in [('maker',f.maker),('reviewer',f.checker),('inspector',f.inspector)]:
            client=Client();client.force_login(user)
            sessions[name]=client.cookies[settings.SESSION_COOKIE_NAME].value
        routes={'list':reverse('returns'),'new':reverse('return_new')+f'?invoice_id={f.invoice.pk}',
                'requested':reverse('return_detail',args=[pending.pk]),'inspected':reverse('return_detail',args=[hold.pk]),
                'transit':reverse('return_detail',args=[transit.pk])}
        out=Path(settings.BASE_DIR)/'test-results'/'returns';out.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as p:
            browser=p.chromium.launch();errors=[];contexts={}
            for name,cookie in sessions.items():
                context=browser.new_context()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                contexts[name]=context
            page=contexts['maker'].new_page();page.on('pageerror',lambda error:errors.append(str(error)))
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
                        page.screenshot(path=str(out/f'{name}-{size}.png'),full_page=True,animations='disabled')
            reviewer=contexts['reviewer'].new_page();reviewer.on('pageerror',lambda error:errors.append(str(error)))
            reviewer.goto(self.live_server_url+routes['requested'])
            reviewer.get_by_label('Decision note').fill('Invoice and pickup quantities independently checked')
            reviewer.get_by_role('button',name='Approve',exact=True).click();reviewer.wait_for_load_state('networkidle')
            inspector=contexts['inspector'].new_page();inspector.on('pageerror',lambda error:errors.append(str(error)))
            inspector.goto(self.live_server_url+routes['requested'])
            inspector.get_by_label('Vehicle',exact=True).fill('MP04GA1234')
            inspector.get_by_role('button',name='Record pickup',exact=True).click();inspector.wait_for_load_state('networkidle')
            inspector.get_by_label('Received',exact=True).fill('4')
            inspector.get_by_label('Good',exact=True).fill('3')
            inspector.get_by_label('Damaged',exact=True).fill('1')
            inspector.get_by_role('button',name='Save inspection',exact=True).click();inspector.wait_for_load_state('networkidle')
            self.assertNotIn('Issue credit note & restock',inspector.locator('main').inner_text())
            reviewer.goto(self.live_server_url+routes['requested'])
            credit_form=reviewer.locator('form').filter(has=reviewer.get_by_role('button',name='Issue credit note & restock',exact=True))
            payload=credit_form.evaluate('(el)=>Object.fromEntries(new FormData(el))')
            reviewer.on('dialog',lambda dialog:dialog.accept())
            reviewer.get_by_role('button',name='Issue credit note & restock',exact=True).click();reviewer.wait_for_load_state('networkidle')
            retry=contexts['reviewer'].request.post(self.live_server_url+reverse('return_action',args=[pending.pk,'credit']),form=payload)
            self.assertEqual(retry.status,200)
            self.assertEqual(errors,[])
            browser.close()
        pending.refresh_from_db()
        self.assertEqual(pending.status,'credited')
        self.assertEqual(CreditNote.objects.count(),1)
        self.assertEqual(StockBalance.objects.get(site=f.site,product=f.bag,status='available').quantity,3)
        self.assertEqual(StockBalance.objects.get(site=f.site,product=f.bag,status='damaged').quantity,1)
