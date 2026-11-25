"""Maintenance on one page: KPIs, report a job, open jobs with their next action, preventive schedule and equipment."""
from datetime import date

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import maintenance as M
from .models import Equipment, MaintenancePlan, MaintenanceJob, Site
from .services import permitted, require_site


def _sites(user):
    qs = Site.objects.filter(active=True).order_by('code')
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


@login_required
def maintenance(request):
    permitted(request.user, 'view_maintenancejob')
    sites = list(_sites(request.user))
    G = request.GET
    status = G.get('status', 'open')
    jobs = MaintenanceJob.objects.filter(equipment__site__in=sites).select_related('equipment__site', 'assigned_to', 'reported_by', 'verified_by')
    if status == 'open': jobs = jobs.filter(status__in=['open', 'progress', 'done'])
    elif status != 'all': jobs = jobs.filter(status=status)
    if G.get('q'): jobs = jobs.filter(Q(number__icontains=G['q']) | Q(equipment__name__icontains=G['q']) | Q(equipment__code__icontains=G['q']) | Q(description__icontains=G['q']))
    equipment = Equipment.objects.filter(site__in=sites).select_related('site')
    U = get_user_model()
    return render(request, 'erp/maintenance.html', {
        'title': 'Maintenance', 'kpi': M.kpis(sites), 'jobs': jobs[:100], 'status': status, 'q': G.get('q', ''), 'equipment': equipment,
        'active_equipment': [e for e in equipment if e.active], 'plans': MaintenancePlan.objects.filter(equipment__site__in=sites).select_related('equipment__site'),
        'sites': sites, 'people': U.objects.filter(is_active=True).order_by('first_name', 'username'), 'today': timezone.localdate(),
        'can': {k: request.user.has_perm('erp.' + p) for k, p in (('report', 'add_maintenancejob'), ('work', 'change_maintenancejob'), ('verify', 'verify_maintenancejob'),
                                                                  ('equip', 'add_equipment'), ('plan', 'add_maintenanceplan'))}})


@login_required
@require_POST
def maintenance_action(request, action):
    P, u = request.POST, request.user
    try:
        if action == 'report':
            eq = Equipment.objects.get(pk=P.get('equipment') or 0)
            job = M.report(u, eq, P.get('kind', ''), P.get('description', ''), P.get('priority', 'normal'), date.fromisoformat(P['due_on']) if P.get('due_on') else None)
            msg = f'{job.number} opened.'
        elif action in ('start', 'finish', 'verify', 'sendback', 'cancel'):
            pk = int(P.get('job') or 0)
            if action == 'start':
                M.start(u, pk, get_user_model().objects.filter(pk=P.get('technician') or 0, is_active=True).first()); msg = 'Job started.'
            elif action == 'finish':
                M.finish(u, pk, P.get('work_done', ''), P.get('spares', ''), int(P.get('downtime') or 0)); msg = 'Marked done — waiting for verification.'
            elif action in ('verify', 'sendback'):
                M.verify(u, pk, action == 'verify', P.get('note', '')); msg = 'Job closed.' if action == 'verify' else 'Sent back to the technician.'
            else:
                M.cancel(u, pk, P.get('note', '')); msg = 'Job cancelled.'
        elif action == 'equipment':
            permitted(u, 'add_equipment')
            site = Site.objects.get(pk=P.get('site') or 0)
            require_site(u, site)
            code = (P.get('code') or '').strip().upper()
            if not code or not (P.get('name') or '').strip():
                raise ValidationError('Enter the equipment code and name.')
            if Equipment.objects.filter(site=site, code=code).exists():
                raise ValidationError(f'{code} already exists at {site.code}.')
            Equipment.objects.create(site=site, code=code[:30], name=P['name'].strip()[:100], kind=P.get('kind', 'machine'), criticality=P.get('criticality', 'medium'))
            msg = f'{code} added.'
        elif action == 'plan':
            permitted(u, 'add_maintenanceplan')
            eq = Equipment.objects.get(pk=P.get('equipment') or 0)
            require_site(u, eq.site)
            every = int(P.get('every_days') or 0)
            if every < 1 or not (P.get('task') or '').strip():
                raise ValidationError('Enter the task and how often (days).')
            MaintenancePlan.objects.create(equipment=eq, task=P['task'].strip()[:150], every_days=every, next_due=date.fromisoformat(P.get('next_due') or timezone.localdate().isoformat()))
            msg = 'Preventive schedule added.'
        elif action == 'open_due':
            made = M.open_due(u, list(_sites(u))); msg = f'{len(made)} preventive job(s) opened.'
        else:
            raise Http404
        messages.success(request, msg)
    except (ValueError, Equipment.DoesNotExist, Site.DoesNotExist):
        messages.error(request, 'Choose the equipment / plant and enter numbers and dates correctly.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('maintenance')
