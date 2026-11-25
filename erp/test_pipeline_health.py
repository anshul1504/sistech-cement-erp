"""Forecast accuracy, workflow handoff and scoped pipeline-board regressions."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import Permission, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import pipeline, projects
from .models import SalesProject, ProjectActivity, ProjectEvent, CRMLead, SiteAccess
from .test_projects import seed_projects, make_project, project_data


@override_settings(EMAIL_OTP_ENABLED=False)
class PipelineHealthTests(TestCase):
    def setUp(self):
        seed_projects(self)
        self.client.force_login(self.rep)
        self.rep.user_permissions.add(Permission.objects.get(codename='export_salesproject'), Permission.objects.get(codename='export_crmlead'))

    def lead(self, **changes):
        fields=dict(number='HEALTH-LEAD',site=self.site,owner=self.rep,business_name='Health Prospect',mobile='9876543210',created_by=self.rep,estimated_value=500,next_follow_up=self.today)
        fields.update(changes)
        return CRMLead.objects.create(**fields)

    def test_board_filters_forecast_and_export_share_the_same_scope(self):
        p=make_project(self,name='Visible forecast',estimated_value='1000')
        hidden=make_project(self,name='Hidden peer forecast',estimated_value='9999')
        SalesProject.objects.filter(pk=hidden.pk).update(owner=self.maker)
        response=self.client.get(reverse('projects'),{'view':'board','owner':self.rep.pk,'site':self.site.pk,'q':'Visible'})
        self.assertContains(response,'Visible forecast')
        self.assertNotContains(response,'Hidden peer forecast')
        self.assertEqual(response.context['open_value'],Decimal('1000'))
        self.assertEqual(response.context['weighted'],Decimal('100'))
        self.assertEqual(response.context['forecast'][0]['value'],Decimal('1000'))
        self.assertEqual(sum(row['count'] for row in response.context['stages']),1)
        self.assertIn('owner=',response.context['stages'][0]['url'])
        exported=self.client.get(reverse('projects'),{'export':'csv','view':'board','q':'Visible'})
        self.assertContains(exported,p.number)
        self.assertNotContains(exported,hidden.number)
        self.assertContains(exported,'Days since stage review')
        self.client.force_login(self.outsider)
        self.assertNotContains(self.client.get(reverse('projects'),{'view':'board','owner':self.rep.pk}),p.name)

    def test_stale_contact_slipped_close_and_stage_age_use_retained_evidence(self):
        p=make_project(self)
        old=timezone.now()-timedelta(days=20)
        SalesProject.objects.filter(pk=p.pk).update(created_at=old,expected_close=self.today-timedelta(days=1),next_action_on=self.today-timedelta(days=2))
        ProjectEvent.objects.filter(project=p).update(created_at=old)
        for risk in ['slipped','stale']:
            response=self.client.get(reverse('projects'),{'risk':risk})
            self.assertContains(response,p.name)
            self.assertEqual(response.context['stale'],1)
        response=self.client.get(reverse('project_detail',args=[p.pk]))
        self.assertContains(response,'Expected close missed')
        self.assertContains(response,'Follow-up overdue')
        self.assertEqual(response.context['health']['stage_days'],20)
        ProjectActivity.objects.create(project=p,kind='call',on=self.today,note='Current conversation',next_action_on=self.today,created_by=self.rep)
        response=self.client.get(reverse('projects'),{'risk':'stale'})
        self.assertNotContains(response,p.name)

    def test_forecast_excludes_closed_and_keeps_held_weight_zero(self):
        p=make_project(self,name='Open 100',estimated_value='100')
        held=make_project(self,name='Held 250',estimated_value='250',expected_close='')
        projects.change_stage(self.rep,held.pk,'hold','Waiting on customer',self.today)
        lost=make_project(self,name='Lost 1000',estimated_value='1000')
        projects.change_stage(self.rep,lost.pk,'lost','Customer chose alternative')
        response=self.client.get(reverse('projects'))
        self.assertEqual(response.context['open_value'],Decimal('350'))
        self.assertEqual(response.context['weighted'],Decimal('10'))
        self.assertEqual(response.context['unscheduled'],1)
        self.assertEqual(sum(r['value'] for r in response.context['forecast']),Decimal('350'))
        self.assertEqual(next(r for r in response.context['forecast'] if r['month']=='Not scheduled')['weighted'],0)

    def test_invalid_close_range_and_owner_cannot_widen_access(self):
        p=make_project(self)
        for values in [{'close_from':'bad'},{'close_from':'2026-12-01','close_to':'2026-01-01'}]:
            for export in ['', 'csv']:
                self.assertEqual(self.client.get(reverse('projects'),{**values,'export':export}).status_code,400)
        for owner in ['invalid','9'*100,'-1']:
            self.assertNotContains(self.client.get(reverse('projects'),{'owner':owner}),p.name)

    def test_crm_board_filters_counts_and_missing_followup(self):
        own=self.lead(next_follow_up=None)
        self.lead(number='HIDDEN',mobile='8765432109',owner=self.maker,business_name='Hidden CRM buyer',estimated_value=9999)
        response=self.client.get(reverse('crm_leads'),{'view':'board','kind':'Dealer','due':'missing'})
        self.assertContains(response,own.business_name)
        self.assertNotContains(response,'Hidden CRM buyer')
        self.assertContains(response,'Schedule the next conversation')
        self.assertEqual(response.context['open_count'],1)
        self.assertEqual(response.context['opportunity'],Decimal('500'))
        self.assertEqual(sum(row['count'] for row in response.context['stages']),1)
        self.assertIn('due=missing',response.context['stages'][0]['url'])

    def test_crm_to_project_prefill_preserves_source_and_rejects_private_lead(self):
        source=self.lead()
        response=self.client.get(reverse('project_new'),{'lead':source.pk})
        self.assertEqual(response.status_code,200)
        form=response.context['form']
        self.assertEqual(form.initial['lead'],source.pk)
        self.assertEqual(form.initial['prospect_name'],source.business_name)
        self.assertEqual(form.initial['site'],source.site_id)
        private=self.lead(number='PRIVATE',mobile='8765432109',owner=self.maker)
        self.assertEqual(self.client.get(reverse('project_new'),{'lead':private.pk}).status_code,404)
        self.assertEqual(self.client.get(reverse('project_new'),{'lead':'bad'}).status_code,404)
        source.stage='lost';source.save()
        self.assertEqual(self.client.get(reverse('project_new'),{'lead':source.pk}).status_code,404)
        self.assertEqual(SalesProject.objects.count(),0)

    def test_stage_board_is_read_only_and_cards_are_capped(self):
        for i in range(14):make_project(self,name=f'Capped project {i}')
        events=ProjectEvent.objects.count()
        response=self.client.get(reverse('projects'),{'view':'board','stage':'new'})
        column=next(c for c in response.context['columns'] if c['key']=='new')
        self.assertEqual(len(column['cards']),12)
        self.assertEqual(column['more'],2)
        self.assertEqual(ProjectEvent.objects.count(),events)
        self.assertEqual(SalesProject.objects.filter(stage='new').count(),14)
