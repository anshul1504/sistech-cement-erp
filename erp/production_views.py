"""Production & quality screens: process orders, recipes, shift log, packing, lab results, equipment, COA and batch trace."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import production as PR
from . import quality as QC
from .models import (Production, Recipe, Product, Site, ShiftLog, PackingRun, QualitySpec, LabEquipment, QualityResult, GoodsReceipt, AuditEvent,
                     Organization, DOWNTIME_REASONS, SHIFTS, StockBalance)
from .services import permitted, scope

MAKE = ['Cement', 'TMT', 'Raw material']


def _sites(user):
    qs = Site.objects.filter(active=True).order_by('code')
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


def _dec(value, default=None):
    text = str(value or '').replace(',', '').strip()
    return Decimal(text) if text else default


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise ValidationError('Enter a valid date.')


# ---------- Process orders ----------

@login_required
def production(request):
    permitted(request.user, 'view_production')
    qs = scope(Production.objects.select_related('site', 'product', 'recipe'), request.user)
    status, q = request.GET.get('status', ''), request.GET.get('q', '').strip()
    base = qs
    if status: qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(batch__icontains=q) | Q(product__name__icontains=q) | Q(line__icontains=q))
    today = timezone.localdate()
    alerts = AuditEvent.objects.filter(action='Quality alert', created_at__date__gte=today - timedelta(days=30)).count()
    return render(request, 'erp/production.html', {
        'title': 'Production', 'orders': qs[:200], 'status': status, 'q': q, 'statuses': [s for s in ['Planned', 'In progress', 'Quality hold', 'Released', 'Rejected', 'Cancelled']],
        'kpi': {'running': base.filter(status='In progress').count(), 'hold': base.filter(status='Quality hold').count(),
                'today': base.filter(status='Released', released_at__date=today).aggregate(q=Sum('actual_quantity'))['q'] or 0, 'alerts': alerts},
        'can_add': request.user.has_perm('erp.add_production')})


@login_required
def production_new(request):
    permitted(request.user, 'add_production')
    sites = list(_sites(request.user))
    products = list(Product.objects.filter(active=True, category__in=MAKE).order_by('category', 'name'))
    P, errors = request.POST, []
    if request.method == 'POST':
        site = next((s for s in sites if str(s.pk) == P.get('site')), None)
        product = next((p for p in products if str(p.pk) == P.get('product')), None)
        if not site or not product:
            errors.append('Choose the plant and the product.')
        else:
            try:
                p = PR.create_order(request.user, site, product, _dec(P.get('planned_quantity'), 0), P.get('shift', ''), P.get('line', ''), _date(P.get('planned_date')),
                                    P.get('batch', ''), P.get('notes', ''))
            except (ValidationError, InvalidOperation) as e:
                errors += getattr(e, 'messages', ['Enter the planned output as a number.'])
            else:
                messages.success(request, f'Process order {p.batch} created.' + ('' if p.recipe else ' No approved recipe — material use will not be checked.'))
                return redirect('production_detail', pk=p.pk)
    recipes = {r.product_id: r.version for r in Recipe.objects.filter(status='approved')}
    return render(request, 'erp/production_new.html', {'title': 'New process order', 'sites': sites, 'products': products, 'recipes': recipes, 'errors': errors,
                                                       'post': P, 'shifts': SHIFTS, 'today': timezone.localdate().isoformat()})


@login_required
def production_detail(request, pk):
    permitted(request.user, 'view_production')
    p = get_object_or_404(scope(Production.objects.select_related('site', 'product', 'recipe', 'created_by', 'completed_by', 'released_by'), request.user), pk=pk)
    u = request.user
    output = p.actual_quantity if p.status not in ('Planned', 'In progress') else (_dec(request.GET.get('output')) or p.planned_quantity)
    ev = QC.evidence(p)
    materials = sorted({l.material for l in p.recipe.lines.select_related('material')} if p.recipe else set(), key=lambda m: m.name) or \
        list(Product.objects.filter(active=True).exclude(pk=p.product_id).exclude(category__in=['Cement', 'TMT']).order_by('name'))
    for m in materials:
        m.on_hand = StockBalance.objects.filter(site=p.site, product=m, status='available').values_list('quantity', flat=True).first() or Decimal('0')
    equipment = [e for e in LabEquipment.objects.filter(site=p.site, active=True)]
    return render(request, 'erp/production_detail.html', {
        'title': p.batch, 'p': p, 'variance': PR.variance(p, output + (p.scrap_quantity or 0)), 'output': output, 'ev': ev, 'materials': materials,
        'issues': p.issues.select_related('material', 'grn', 'issued_by'), 'equipment': equipment,
        'grns': GoodsReceipt.objects.filter(site=p.site, status='posted').order_by('-posted_at')[:30],
        'history_results': p.results.filter(superseded_by__isnull=False).select_related('spec', 'analyst'),
        'logs': p.shift_logs.all(), 'history': AuditEvent.objects.filter(reference=p.batch).select_related('actor').order_by('-created_at')[:15],
        'can': {'issue': p.status in ('Planned', 'In progress') and u.has_perm('erp.complete_production'),
                'complete': p.status in ('Planned', 'In progress') and u.has_perm('erp.complete_production'),
                'test': p.status in ('In progress', 'Quality hold', 'Released') and u.has_perm('erp.add_qualitytest'),
                'release': p.status == 'Quality hold' and u.has_perm('erp.release_production') and u.pk not in (p.created_by_id, p.completed_by_id),
                'cancel': p.status == 'Planned' and not p.issues.exists() and u.has_perm('erp.add_production')}})


@login_required
@require_POST
def production_action(request, pk, action):
    p = get_object_or_404(scope(Production.objects.all(), request.user), pk=pk)
    P, u = request.POST, request.user
    try:
        if action == 'issue':
            material = get_object_or_404(Product, pk=P.get('material') or 0)
            grn = GoodsReceipt.objects.filter(pk=P.get('grn') or 0).first()
            i = PR.issue(u, pk, material, _dec(P.get('quantity'), 0), P.get('source', ''), grn)
            msg = f'{i.material.name}: {i.quantity.normalize():f} {i.material.unit} issued.'
        elif action == 'complete':
            PR.complete(u, pk, _dec(P.get('output'), p.actual_quantity), _dec(P.get('scrap'), 0), P.get('note', ''))
            msg = 'Batch complete and on quality hold. The lab records results next.'
        elif action == 'result':
            spec = get_object_or_404(QualitySpec, pk=P.get('spec') or 0)
            eq = LabEquipment.objects.filter(pk=P.get('equipment') or 0).first()
            r = QC.record_result(u, pk, spec, P.get('value', ''), eq, P.get('sample_ref', ''), P.get('remarks', ''), P.get('retest_reason', ''))
            msg = f'{spec.parameter}: {"pass" if r.ok else "OUT OF SPECIFICATION"}.'
        elif action in ('release', 'deviation', 'reject'):
            QC.release(u, pk, action, P.get('note', ''))
            msg = {'release': 'Batch released to stock.', 'deviation': 'Batch released under deviation.', 'reject': 'Batch rejected.'}[action]
        elif action == 'cancel':
            PR.cancel(u, pk, P.get('note', '')); msg = 'Process order cancelled.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except InvalidOperation:
        messages.error(request, 'Enter numbers only.')
    return redirect('production_detail', pk=pk)


@login_required
def coa(request, pk):
    permitted(request.user, 'view_production')
    p = get_object_or_404(scope(Production.objects.select_related('site', 'product', 'released_by'), request.user), pk=pk)
    if p.status != 'Released':
        messages.error(request, 'The certificate prints after the batch is released.')
        return redirect('production_detail', pk=pk)
    ev = QC.evidence(p)
    return render(request, 'erp/coa.html', {'title': f'Test certificate {p.batch}', 'p': p, 'rows': [r for r in ev['rows'] if r['result']], 'pending': [r for r in ev['rows'] if not r['result']],
                                            'company': Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization(), 'now': timezone.now(),
                                            'customer': request.GET.get('for', '')[:120]})


@login_required
def trace(request, pk):
    permitted(request.user, 'view_production')
    p = get_object_or_404(scope(Production.objects.select_related('site', 'product'), request.user), pk=pk)
    return render(request, 'erp/trace.html', {'title': f'Trace {p.batch}', 'p': p, **QC.trace(p)})


# ---------- Recipes ----------

@login_required
def recipes(request):
    permitted(request.user, 'view_recipe')
    qs = Recipe.objects.select_related('product', 'created_by', 'approved_by').prefetch_related('lines__material')
    return render(request, 'erp/recipes.html', {'title': 'Recipes & BOM', 'recipes': qs, 'can_add': request.user.has_perm('erp.add_recipe'),
                                                'can_approve': request.user.has_perm('erp.approve_recipe')})


@login_required
def recipe_new(request):
    permitted(request.user, 'add_recipe')
    products = list(Product.objects.filter(active=True).order_by('category', 'name'))
    P, errors = request.POST, []
    base = Recipe.objects.filter(pk=request.GET.get('from') or 0).prefetch_related('lines').first()
    rows = [{'material': P.get(f'material_{i}', ''), 'qty': P.get(f'qty_{i}', ''), 'tol': P.get(f'tol_{i}', '2')} for i in range(8)]
    if base and request.method != 'POST':
        for i, l in enumerate(base.lines.all()[:8]):
            rows[i] = {'material': str(l.material_id), 'qty': f'{l.quantity.normalize():f}', 'tol': f'{l.tolerance_pct.normalize():f}'}
    if request.method == 'POST':
        pmap = {str(p.pk): p for p in products}
        product = pmap.get(P.get('product'))
        try:
            lines = [(pmap[r['material']], _dec(r['qty'], 0), _dec(r['tol'], Decimal(2))) for r in rows if r['material'] in pmap and r['qty']]
            if not product:
                raise ValidationError('Choose the product made.')
            r = PR.save_recipe(request.user, product, lines, _dec(P.get('basis'), 1), _dec(P.get('yield'), 100), _date(P.get('effective_from')), P.get('notes', ''))
        except (ValidationError, InvalidOperation) as e:
            errors += getattr(e, 'messages', ['Enter numbers only.'])
        else:
            messages.success(request, f'{r} saved as draft. A different user approves it.')
            return redirect('recipes')
    return render(request, 'erp/recipe_new.html', {'title': 'New recipe version', 'products': products, 'rows': rows, 'errors': errors, 'post': P, 'base': base,
                                                   'chosen': P.get('product') or (str(base.product_id) if base else ''),
                                                   'basis': P.get('basis') or (f'{base.basis_quantity.normalize():f}' if base else '1'),
                                                   'yield_pct': P.get('yield') or (f'{base.expected_yield_pct.normalize():f}' if base else '100')})


@login_required
@require_POST
def recipe_approve(request, pk):
    try:
        r = PR.approve_recipe(request.user, pk)
        messages.success(request, f'{r} approved. Earlier versions retired; new process orders use it.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('recipes')


# ---------- Shift log & packing ----------

@login_required
def shift_logs(request):
    permitted(request.user, 'view_shiftlog')
    sites = list(_sites(request.user))
    P, errors = request.POST, []
    if request.method == 'POST':
        site = next((s for s in sites if str(s.pk) == P.get('site')), None)
        try:
            if not site:
                raise ValidationError('Choose the plant.')
            batch = Production.objects.filter(pk=P.get('production') or 0, site=site).first()
            PR.save_shift_log(request.user, {'site': site, 'date': _date(P.get('date')) or timezone.localdate(), 'shift': P.get('shift', 'A'), 'line': P.get('line', ''),
                                             'production': batch, 'run_hours': _dec(P.get('run_hours'), 0), 'downtime_minutes': int(P.get('downtime_minutes') or 0),
                                             'downtime_reason': P.get('downtime_reason', ''), 'power_kwh': _dec(P.get('power_kwh'), 0), 'output': _dec(P.get('output'), 0),
                                             'meter_opening': _dec(P.get('meter_opening')), 'meter_closing': _dec(P.get('meter_closing')), 'crew': P.get('crew', '')[:150],
                                             'supervisor': P.get('supervisor', ''), 'remarks': P.get('remarks', '')[:200]})
        except (ValidationError, InvalidOperation, ValueError) as e:
            errors += getattr(e, 'messages', ['Enter numbers only.'])
        else:
            messages.success(request, 'Shift logged.')
            return redirect('shift_logs')
    logs = scope(ShiftLog.objects.select_related('site', 'production'), request.user)[:120]
    week = scope(ShiftLog.objects.filter(date__gte=timezone.localdate() - timedelta(days=6)), request.user).aggregate(h=Sum('run_hours'), d=Sum('downtime_minutes'), k=Sum('power_kwh'), o=Sum('output'))
    return render(request, 'erp/shift_logs.html', {'title': 'Shift log', 'logs': logs, 'sites': sites, 'shifts': SHIFTS, 'reasons': DOWNTIME_REASONS[1:], 'errors': errors, 'post': P,
                                                   'week': week, 'running': scope(Production.objects.filter(status__in=['Planned', 'In progress']), request.user),
                                                   'can_add': request.user.has_perm('erp.add_shiftlog'), 'today': timezone.localdate().isoformat()})


@login_required
def packing(request):
    permitted(request.user, 'view_packingrun')
    sites = list(_sites(request.user))
    bulk = list(Product.objects.filter(active=True, unit='MT', category='Cement'))
    bags = list(Product.objects.filter(active=True, unit='BAG'))
    empties = list(Product.objects.filter(active=True, category__in=['Packaging', 'Spares']).exclude(unit='MT'))
    P, errors = request.POST, []
    if request.method == 'POST':
        site = next((s for s in sites if str(s.pk) == P.get('site')), None)
        try:
            if not site:
                raise ValidationError('Choose the plant.')
            pick = lambda items, key: next((x for x in items if str(x.pk) == P.get(key)), None)
            data = {'date': _date(P.get('date')) or timezone.localdate(), 'shift': P.get('shift', 'A'), 'packer': (P.get('packer') or '').strip()[:40] or 'Packer',
                    'bulk_product': pick(bulk, 'bulk_product'), 'bag_product': pick(bags, 'bag_product'),
                    'batch': Production.objects.filter(pk=P.get('batch') or 0, site=site).first(), 'bulk_issued': _dec(P.get('bulk_issued'), 0),
                    'bags_packed': int(P.get('bags_packed') or 0), 'bags_burst': int(P.get('bags_burst') or 0), 'spillage_kg': _dec(P.get('spillage_kg'), 0),
                    'downtime_minutes': int(P.get('downtime_minutes') or 0), 'empty_bags': pick(empties, 'empty_bags'), 'difference_reason': P.get('difference_reason', '')}
            if not data['bulk_product'] or not data['bag_product']:
                raise ValidationError('Choose the bulk cement and the bagged SKU.')
            run = PR.pack(request.user, site, data)
        except (ValidationError, InvalidOperation, ValueError) as e:
            errors += getattr(e, 'messages', ['Enter numbers only.'])
        else:
            messages.success(request, f'{run.number}: {run.bags_packed} bags to stock at ₹{run.unit_cost:.2f} per bag.')
            return redirect('packing')
    runs = scope(PackingRun.objects.select_related('site', 'bulk_product', 'bag_product', 'batch'), request.user)[:100]
    return render(request, 'erp/packing.html', {'title': 'Packing', 'runs': runs, 'sites': sites, 'bulk': bulk, 'bags': bags, 'empties': empties, 'shifts': SHIFTS,
                                                'batches': scope(Production.objects.filter(status='Released'), request.user).order_by('-released_at')[:30],
                                                'errors': errors, 'post': P, 'can_add': request.user.has_perm('erp.add_packingrun'), 'today': timezone.localdate().isoformat()})


# ---------- Quality lab ----------

@login_required
def quality(request):
    permitted(request.user, 'view_qualitytest')
    held = list(scope(Production.objects.filter(status='Quality hold').select_related('site', 'product'), request.user))
    for p in held:
        p.ev = QC.evidence(p)
    today = timezone.localdate()
    eq = scope(LabEquipment.objects.filter(active=True).select_related('site'), request.user)
    return render(request, 'erp/quality.html', {
        'title': 'Quality lab', 'held': held, 'equipment': eq, 'due_soon': [e for e in eq if e.due_days <= 15],
        'oos': scope(QualityResult.objects.filter(ok=False, superseded_by__isnull=True, tested_at__date__gte=today - timedelta(days=60)).select_related('production', 'spec'), request.user, 'production__site')[:30],
        'alerts': AuditEvent.objects.filter(action='Quality alert').order_by('-created_at')[:10],
        'pending': [(p, r['spec']) for p in scope(Production.objects.filter(status='Released', released_at__date__gte=today - timedelta(days=35)).select_related('product'), request.user)
                    for r in QC.evidence(p)['rows'] if not r['result'] and r['due'] and r['due'] <= today],
        'spec_groups': _spec_groups(),
        'sites': _sites(request.user), 'can_eq': request.user.has_perm('erp.add_labequipment')})


def _spec_groups():
    groups = {}
    for s in QualitySpec.objects.filter(active=True).select_related('product').order_by('product__name', 'sequence'):
        groups.setdefault(s.product, []).append(s)
    return list(groups.items())


@login_required
@require_POST
def equipment_save(request):
    site = next((s for s in _sites(request.user) if str(s.pk) == request.POST.get('site')), None)
    try:
        if not site:
            raise ValidationError('Choose the plant.')
        P = request.POST
        QC.save_equipment(request.user, site, P.get('code'), P.get('name'), _date(P.get('due')), _date(P.get('last')), P.get('certificate', ''), P.get('pk') or None)
        messages.success(request, 'Lab equipment saved.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('quality')
