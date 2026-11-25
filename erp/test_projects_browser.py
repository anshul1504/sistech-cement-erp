"""Opt-in project pipeline layouts and quotation preparation/stage evidence browser QA."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import skipUnless
from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import Client,override_settings
from django.urls import reverse
from . import projects
from .models import SalesProject,ProjectActivity,ProjectLink,Quotation,Order
from .test_projects import seed_projects,make_project,sent_quote


@skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1' and importlib.util.find_spec('playwright'),'Opt-in project browser QA')
@override_settings(EMAIL_OTP_ENABLED=False)
class ProjectBrowserTests(StaticLiveServerTestCase):
    def test_project_pages_source_link_activity_stage_and_quote_handoff(self):
        from playwright.sync_api import sync_playwright
        f=SimpleNamespace();seed_projects(f)
        p=make_project(f);q=sent_quote(f)
        closed=make_project(f,name='Fictional closed construction project')
        order=Order.objects.create(site=f.site,partner=f.customer,kind='Sale',created_by=f.maker,status='Approved',approved_by=f.checker)
        projects.link(f.rep,closed.pk,dict(order=order,quotation=None,note='QA source order'))
        projects.change_stage(f.rep,closed.pk,'won','QA approved order confirmed')
        rep=Client();rep.force_login(f.rep)
        manager=Client();manager.force_login(f.maker)
        routes=[('pipeline','rep',reverse('projects')),('pipeline-board','rep',reverse('projects')+'?view=board'),('new','rep',reverse('project_new')),('edit','rep',reverse('project_edit',args=[p.pk])),
                ('detail','rep',reverse('project_detail',args=[p.pk])),('closed','manager',reverse('project_detail',args=[closed.pk])),
                ('quotation-handoff','rep',reverse('quotation_new')+f'?project_id={p.pk}')]
        out=Path(settings.BASE_DIR)/'test-results'/'projects';out.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as pw:
            browser=pw.chromium.launch();pages={};errors=[]
            for name,client in [('rep',rep),('manager',manager)]:
                ctx=browser.new_context();ctx.add_cookies([dict(name=settings.SESSION_COOKIE_NAME,value=client.cookies[settings.SESSION_COOKIE_NAME].value,url=self.live_server_url)])
                pages[name]=ctx.new_page();pages[name].on('pageerror',lambda error:errors.append(str(error)))
            for size,width,height in [('small-phone',320,720),('phone',390,844),('tablet',768,1024),('desktop',1440,900),('desktop-collapsed',1470,834)]:
                for name,actor,path in routes:
                    page=pages[actor];page.set_viewport_size(dict(width=width,height=height))
                    with self.subTest(size=size,route=name):
                        response=page.goto(self.live_server_url+path);self.assertEqual(response.status,200)
                        page.evaluate('document.fonts.ready')
                        self.assertIn('Poppins',page.evaluate('getComputedStyle(document.body).fontFamily'))
                        if size.endswith('collapsed') and not page.locator('html').evaluate("el=>el.classList.contains('sb-collapsed')"):
                            page.get_by_role('button',name='Collapse sidebar',exact=True).click()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'),f'{name} overflows at {width}px')
                        self.assertTrue(page.evaluate("(()=>{const ids=[...document.querySelectorAll('[id]')].map(el=>el.id);return ids.length===new Set(ids).size;})()"),f'{name} has duplicate IDs')
                        page.screenshot(path=str(out/f'{name}-{size}.png'),full_page=True,animations='disabled')
            page=pages['rep'];page.goto(self.live_server_url+reverse('project_detail',args=[p.pk]))
            page.locator('[name=source-quotation]').select_option(str(q.pk))
            page.locator('[name=source-note]').fill('Offer matched to construction project')
            page.get_by_role('button',name='Link source',exact=True).click();page.wait_for_load_state('networkidle')
            page.locator('[name=activity-kind]').select_option('sample')
            page.locator('[name=activity-on]').fill(str(f.today))
            page.locator('[name=activity-note]').fill('QA technical sample discussed at construction site')
            page.locator('[name=activity-next_action_on]').fill(str(f.today))
            page.get_by_role('button',name='Save activity',exact=True).click();page.wait_for_load_state('networkidle')
            page.locator('#project-stage').select_option('quoted')
            page.locator('#stage-note').fill('Sent quotation evidence confirmed')
            page.get_by_role('button',name='Record stage',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('Quoted',page.locator('main').inner_text())
            page.get_by_role('link',name='Prepare quotation',exact=True).click();page.wait_for_load_state('networkidle')
            page.locator('[name=product_0]').select_option(str(f.bag.pk));page.locator('[name=qty_0]').fill('10');page.locator('[name=rate_0]').fill('100')
            page.get_by_role('button',name='Save draft',exact=True).click();page.wait_for_load_state('networkidle')
            self.assertIn('/quotations/',page.url)
            self.assertEqual(errors,[]);browser.close()
        p.refresh_from_db();self.assertEqual(p.stage,'quoted')
        self.assertEqual(ProjectActivity.objects.filter(project=p).count(),1)
        self.assertEqual(ProjectLink.objects.filter(project=p,active=True).count(),2)
        self.assertEqual(Quotation.objects.count(),2)
