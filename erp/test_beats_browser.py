"""Opt-in territory/route layouts and independent field-to-collections browser flow."""
import importlib.util
import os
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from .models import BeatPlan,BeatStop,CollectionFollowUp,Receipt
from .test_beats import seed_beats,plan_draft,approved_plan


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in territory and beat browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class BeatBrowserTests(StaticLiveServerTestCase):
    def test_layouts_route_review_outcome_and_finance_promise(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_beats(f)
        plan,stop=plan_draft(f)
        future,_=approved_plan(f,f.today+timedelta(days=1))
        sessions={}
        for name,user in [('rep',f.rep),('manager',f.maker),('reviewer',f.checker)]:
            client=Client();client.force_login(user);sessions[name]=client.cookies[settings.SESSION_COOKIE_NAME].value
        routes=[('plans','rep',reverse('beat_plans')),('territories','rep',reverse('territories')),('coverage','rep',reverse('beat_report')),
                ('new-plan','rep',reverse('beat_new')),('draft','rep',reverse('beat_detail',args=[plan.pk])),('approved','rep',reverse('beat_detail',args=[future.pk])),
                ('new-territory','manager',reverse('territory_new',args=['territory'])),('new-assignment','manager',reverse('territory_new',args=['assignment']))]
        out=Path(settings.BASE_DIR)/'test-results'/'beats';out.mkdir(parents=True,exist_ok=True)
        sizes=[('small-phone',320,720),('phone',390,844),('tablet',768,1024),('desktop',1440,900),('desktop-collapsed',1470,834)]
        with sync_playwright() as p:
            browser=p.chromium.launch();contexts={};pages={};errors=[]
            for name,cookie in sessions.items():
                ctx=browser.new_context();ctx.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=cookie,url=self.live_server_url)])
                contexts[name]=ctx;pages[name]=ctx.new_page();pages[name].on('pageerror',lambda error:errors.append(str(error)))
            for label,width,height in sizes:
                for name,actor,path in routes:
                    page=pages[actor];page.set_viewport_size(dict(width=width,height=height))
                    with self.subTest(size=label,route=name):
                        response=page.goto(self.live_server_url+path);self.assertEqual(response.status,200)
                        page.evaluate('document.fonts.ready')
                        self.assertIn('Poppins',page.evaluate('getComputedStyle(document.body).fontFamily'))
                        if label.endswith('collapsed') and not page.locator('html').evaluate("el=>el.classList.contains('sb-collapsed')"):
                            page.get_by_role('button',name='Collapse sidebar',exact=True).click()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'{name} overflows at {width}px')
                        page.screenshot(path=str(out/f'{name}-{label}.png'),full_page=True,animations='disabled')
            rep=pages['rep'];reviewer=pages['reviewer']
            rep.goto(self.live_server_url+reverse('beat_detail',args=[plan.pk]))
            rep.locator('#plan-submit').fill('Route assignments and sequence checked')
            rep.get_by_role('button',name='Submit plan',exact=True).click();rep.wait_for_load_state('networkidle')
            reviewer.goto(self.live_server_url+reverse('beat_detail',args=[plan.pk]))
            reviewer.locator('#plan-review').fill('Independent route approval')
            reviewer.get_by_role('button',name='Approve plan',exact=True).click();reviewer.wait_for_load_state('networkidle')
            for label,width,height in sizes:
                rep.set_viewport_size(dict(width=width,height=height));rep.goto(self.live_server_url+reverse('beat_outcome',args=[stop.pk]))
                self.assertTrue(rep.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                rep.screenshot(path=str(out/f'outcome-{label}.png'),full_page=True,animations='disabled')
            rep.locator('[name=outcome]').select_option('promise')
            rep.locator('[name=note]').fill('Fictional dealer confirmed payment in two days')
            rep.locator('[name=promised_amount]').fill('500.25')
            rep.locator('[name=promised_on]').fill(str(f.today+timedelta(days=2)))
            rep.locator('[name=next_action_on]').fill(str(f.today))
            rep.get_by_role('button',name='Save',exact=True).click();rep.wait_for_load_state('networkidle')
            self.assertIn('Awaiting independent finance acceptance',rep.locator('main').inner_text())
            reviewer.goto(self.live_server_url+reverse('beat_detail',args=[plan.pk]))
            reviewer.locator(f'#promise-note-{stop.pk}').fill('Payment promise checked with dealer')
            form=reviewer.locator('form').filter(has=reviewer.get_by_role('button',name='Accept promise into collections',exact=True))
            payload=form.evaluate('(el)=>Object.fromEntries(new FormData(el))')
            reviewer.get_by_role('button',name='Accept promise into collections',exact=True).click();reviewer.wait_for_load_state('networkidle')
            response=contexts['reviewer'].request.post(self.live_server_url+reverse('beat_stop_action',args=[stop.pk,'promise']),form=payload)
            self.assertEqual(response.status,200)
            self.assertIn('Accepted into collections',reviewer.locator('main').inner_text())
            self.assertEqual(errors,[]);browser.close()
        self.assertEqual(BeatPlan.objects.get(pk=plan.pk).status,'finished')
        self.assertEqual(BeatStop.objects.get(pk=stop.pk).status,'completed')
        self.assertEqual(CollectionFollowUp.objects.count(),1)
        self.assertEqual(Receipt.objects.count(),0)
