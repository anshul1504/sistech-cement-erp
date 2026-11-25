"""Quality sampling plan (SRS FR-QC-001) and the lab's 'samples due' list.

Due rules per active plan and plant:
  truck  (incoming) — posted GRN lines of the product still on quality hold;
  batch  (finished / process / dispatch) — batches of the product completed in the last 30 days that miss a required
         parameter of the plan (or of the product specification when the plan lists none);
  shift / daily / weekly — no current result for the product at that plant in the current shift (8 h) / today / last 7 days.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError, PermissionDenied
from django.urls import reverse
from django.utils import timezone

from .models import SamplingPlan, Production, GRNLine, QualityResult, AuditEvent, SAMPLING_STAGES, SAMPLING_FREQUENCIES


def save_plan(user, product, stage, frequency, site=None, parameters=(), sample_size='', note='', pk=None):
    if not user.has_perm('erp.add_samplingplan' if not pk else 'erp.change_samplingplan'):
        raise PermissionDenied
    if stage not in dict(SAMPLING_STAGES) or frequency not in dict(SAMPLING_FREQUENCIES):
        raise ValidationError('Choose the stage and frequency.')
    if frequency == 'truck' and stage != 'incoming':
        raise ValidationError('"Every truck" applies to incoming material.')
    if stage == 'incoming' and frequency == 'batch':
        raise ValidationError('Incoming material is sampled per truck or per period.')
    bad = [p for p in parameters if p.product_id != product.pk]
    if bad:
        raise ValidationError('Parameters must belong to the product specification.')
    plan = SamplingPlan.objects.get(pk=pk) if pk else SamplingPlan(created_by=user)
    plan.product, plan.stage, plan.frequency, plan.site, plan.sample_size, plan.note = product, stage, frequency, site, (sample_size or '')[:80], (note or '')[:200]
    plan.save()
    plan.parameters.set(parameters)
    AuditEvent.objects.create(actor=user, site=site, action='Sampling plan saved', reference=f'sampling:{plan.pk}', detail=str(plan)[:250])
    return plan


def due(sites, now=None):
    now = now or timezone.now()
    today = timezone.localdate()
    out = []
    for plan in SamplingPlan.objects.filter(active=True).select_related('product', 'site').prefetch_related('parameters'):
        plan_sites = [s for s in sites if plan.site_id in (None, s.pk)]
        params = list(plan.parameters.all())
        for site in plan_sites:
            if plan.frequency == 'truck':
                for l in GRNLine.objects.filter(product=plan.product, grn__site=site, grn__status='posted', hold__gt=0, qc_at__isnull=True).select_related('grn'):
                    out.append({'plan': plan, 'site': site, 'what': f'Truck {l.grn.vehicle_number} · {l.grn.number}', 'url': reverse('grn_detail', args=[l.grn_id]), 'kind': 'grn'})
            elif plan.frequency == 'batch':
                since = now - timedelta(days=30)
                for b in Production.objects.filter(product=plan.product, site=site, status__in=['Quality hold', 'Released', 'In progress'], completed_at__gte=since):
                    have = set(QualityResult.objects.filter(production=b, superseded_by__isnull=True).values_list('spec_id', flat=True))
                    need = [p for p in (params or [s for s in plan.product.specs.filter(active=True, required_for_release=True)]) if p.pk not in have]
                    if need:
                        out.append({'plan': plan, 'site': site, 'what': f'Batch {b.batch} · missing ' + ', '.join(p.parameter for p in need[:4]), 'url': reverse('production_detail', args=[b.pk]), 'kind': 'batch'})
            else:
                window = {'shift': timedelta(hours=8), 'daily': timedelta(days=1), 'weekly': timedelta(days=7)}[plan.frequency]
                recent = QualityResult.objects.filter(production__product=plan.product, production__site=site, superseded_by__isnull=True, tested_at__gte=now - window)
                if params:
                    recent = recent.filter(spec__in=params)
                if not recent.exists():
                    out.append({'plan': plan, 'site': site, 'what': f'{plan.get_frequency_display()} sample — none recorded in the last {"8 hours" if plan.frequency == "shift" else "day" if plan.frequency == "daily" else "7 days"}',
                                'url': reverse('quality'), 'kind': 'period'})
    return out
