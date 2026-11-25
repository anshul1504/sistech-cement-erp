"""Opt-in desktop/mobile dealer dashboard, ledger and access-management checks."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client, override_settings
from django.urls import reverse

from accounts.test_support import force_login_recent
from .models import DealerLedgerAccess
from .test_projects import seed_projects
from .test_schemes import invoice_fixture


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in dealer browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class DealerBrowserTests(StaticLiveServerTestCase):
    def test_dealer_pages_downloads_and_administrator_link(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_projects(f)
        invoice_fixture(f,[(f.bag,1000)])
        dealer=User.objects.create_user('browser-dealer',email='dealer@example.test')
        admin=User.objects.create_superuser('browser-admin','admin@example.test','QA-Password-897!')
        DealerLedgerAccess.objects.create(user=dealer,partner=f.customer)
        clients={}
        for label,user in [('dealer',dealer),('admin',admin)]:
            client=Client();force_login_recent(client,user);clients[label]=client
        out=Path(settings.BASE_DIR)/'test-results'/'dealer';out.mkdir(parents=True,exist_ok=True)
        routes=[('dashboard','dealer',reverse('dealer_dashboard')),('invoices','dealer',reverse('dealer_invoices')),
                ('ledger','dealer',reverse('my_ledger')),('people','admin',reverse('people')),
                ('access','admin',reverse('dealer_access',args=[dealer.pk])),('user','admin',reverse('member_detail',args=[dealer.pk]))]
        with sync_playwright() as pw:
            browser=pw.chromium.launch();pages={};errors=[]
            for label,client in clients.items():
                context=browser.new_context()
                context.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
                pages[label]=context.new_page();pages[label].on('pageerror',lambda error:errors.append(str(error)))
            for width,height in [(320,720),(390,844),(768,1024),(1440,900)]:
                for name,label,path in routes:
                    with self.subTest(route=name,width=width):
                        page=pages[label];page.set_viewport_size({'width':width,'height':height})
                        response=page.goto(self.live_server_url+path)
                        self.assertEqual(response.status,200)
                        page.evaluate('document.fonts.ready')
                        self.assertIn('Poppins',page.evaluate('getComputedStyle(document.body).fontFamily'))
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'{name} overflows at {width}px')
                        self.assertTrue(page.evaluate('(()=>{const ids=[...document.querySelectorAll("[id]")].map(el=>el.id);return ids.length===new Set(ids).size;})()'))
                        page.screenshot(path=str(out/f'{name}-{width}.png'),full_page=True,animations='disabled')
            page=pages['dealer'];page.goto(self.live_server_url+reverse('my_ledger'))
            for label,expected in [('Download ledger CSV',b'\xef\xbb\xbf'),('Download PDF',b'%PDF')]:
                with page.expect_download() as download:
                    page.get_by_role('link',name=label,exact=True).click()
                self.assertTrue(Path(download.value.path()).read_bytes().startswith(expected))
            page=pages['admin'];page.goto(self.live_server_url+reverse('dealer_access',args=[dealer.pk]))
            page.get_by_label('Enable dealer self-service').uncheck()
            page.get_by_label('Reason for this access change').fill('Dealer access disabled after account review')
            page.get_by_role('button',name='Save dealer access',exact=True).click();page.wait_for_load_state('networkidle')
            pages['dealer'].goto(self.live_server_url+reverse('dealer_dashboard'))
            self.assertIn(reverse('login'),pages['dealer'].url)
            self.assertEqual(errors,[])
            browser.close()
        self.assertFalse(DealerLedgerAccess.objects.get(user=dealer).active)
