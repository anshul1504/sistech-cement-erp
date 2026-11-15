"""Plan-to-produce (SRS §4.3, FR-PRD-002…010): versioned recipes, process orders, material issue, completion with variance and
actual cost, quality release, shift log and packing with bulk ↔ bag reconciliation.

Controls enforced here (screens and the future API both call these functions):
- recipes are versioned and approved by a different user; approving a version retires the previous one; an order keeps its version;
- material is issued from available stock at average cost (no negative stock), with source silo / lot for traceability;
- completion compares issued quantity with the recipe standard for the actual output; any material outside its tolerance
  needs an explanation; the batch cost is the issued material value divided by good output;
- output enters stock only on quality release by a different user, at the batch's actual cost;
- a packing run reconciles bulk issued (silo meter) with bags × bag weight + spillage; a difference beyond 0.5 % needs a reason
  (never silently written off).
"""
from accounts.delegation import authorize, can_approve
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from .gst import next_number, qtxt, inr
from .models import Recipe, RecipeLine, Production, MaterialIssue, ShiftLog, PackingRun, Product, AuditEvent, GoodsReceipt, Organization
from .services import move_stock, require_site

ZERO = Decimal('0')
PACK_TOLERANCE = Decimal('0.5')


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, site, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, site=site, action=action, reference=str(reference), detail=detail[:250])


# ---------- Recipes ----------

@transaction.atomic
def save_recipe(user, product, lines, basis=1, yield_pct=100, effective_from=None, notes=''):
    """lines: [(material, qty per basis, tolerance %)]. Always creates a new draft version."""
    _need(user, 'add_recipe')
    problems, seen, clean = [], set(), []
    for material, qty, tol in lines:
        qty = Decimal(qty or 0)
        if qty <= 0:
            continue
        if material.pk == product.pk:
            problems.append('A product cannot be its own material.')
        if material.pk in seen:
            problems.append(f'{material.name} is listed twice.')
        seen.add(material.pk)
        clean.append((material, qty, Decimal(tol if tol not in (None, '') else 2)))
    if not clean:
        problems.append('Add at least one material.')
    if Decimal(basis or 0) <= 0:
        problems.append('Output basis must be more than zero.')
    if not (0 < Decimal(yield_pct or 0) <= 100):
        problems.append('Expected yield must be between 0 and 100 %.')
    if problems:
        raise ValidationError(problems)
    Product.objects.select_for_update().get(pk=product.pk)
    version = (Recipe.objects.filter(product=product).aggregate(m=Max('version'))['m'] or 0) + 1
    r = Recipe.objects.create(product=product, version=version, basis_quantity=basis, expected_yield_pct=yield_pct,
                              effective_from=effective_from or timezone.localdate(), notes=(notes or '')[:200], created_by=user)
    for material, qty, tol in clean:
        RecipeLine.objects.create(recipe=r, material=material, quantity=qty, tolerance_pct=tol)
    _log(user, None, 'Recipe drafted', f'{product.code} v{version}', ', '.join(f'{m.code} {qtxt(q)}' for m, q, _ in clean))
    return r


@transaction.atomic
def approve_recipe(user, pk):
    _need(user, 'view_recipe')
    header=Recipe.objects.only('product_id').get(pk=pk)
    Product.objects.select_for_update().get(pk=header.product_id)
    r = Recipe.objects.select_for_update(of=('self',)).select_related('product').get(pk=pk)
    if r.status != 'draft':
        raise ValidationError('Only draft recipes can be approved.')
    if r.created_by_id == user.pk:
        raise ValidationError('The person who drafted the recipe cannot approve it (maker-checker).')
    authorize(user,'approve_recipe',makers=[r.created_by_id],reference=f'recipe:{r.pk}')
    lines=list(r.lines.select_related('material'))
    if not r.product.active or not lines or not r.basis_quantity.is_finite() or r.basis_quantity<=0 or not r.expected_yield_pct.is_finite() or not 0<r.expected_yield_pct<=100:
        raise ValidationError('Approve an active product with a valid output basis, yield and material lines.')
    if any(not line.material.active or line.material_id==r.product_id or not line.quantity.is_finite() or line.quantity<=0 or not line.tolerance_pct.is_finite() or line.tolerance_pct<0 for line in lines):
        raise ValidationError('Recipe materials, quantities and tolerances must be active and valid.')
    if r.effective_from <= timezone.localdate():
        Recipe.objects.filter(product=r.product, status='approved', effective_from__lte=timezone.localdate()).update(status='retired')
    r.status, r.approved_by, r.approved_at = 'approved', user, timezone.now()
    r.save()
    _log(user, None, 'Recipe approved', f'{r.product.code} v{r.version}', 'Earlier versions retired')
    return r


def can_approve_recipe(user,recipe):
    return recipe.status=='draft' and user.has_perm('erp.view_recipe') and user.pk!=recipe.created_by_id and can_approve(user,'approve_recipe',makers=[recipe.created_by_id])


def current_recipe(product, on=None):
    return Recipe.objects.filter(product=product, status='approved', effective_from__lte=on or timezone.localdate()).order_by('-effective_from', '-version').prefetch_related('lines__material').first()


def standard(production, output=None):
    """Material standard for an output quantity: recipe qty per basis × output ÷ yield."""
    r = production.recipe
    if not r:
        return {}
    out = Decimal(output if output is not None else production.planned_quantity)
    factor = out / r.basis_quantity / (r.expected_yield_pct / 100)
    return {l.material: ((l.quantity * factor).quantize(Decimal('0.001')), l.tolerance_pct) for l in r.lines.select_related('material')}


# ---------- Process orders ----------

def next_batch(site, shift, day=None):
    day = day or timezone.localdate()
    stem = f'{site.code}-{day:%y%m%d}-{shift}'
    n = Production.objects.filter(batch__startswith=stem).count() + 1
    return f'{stem}{n:02d}'


@transaction.atomic
def create_order(user, site, product, planned_quantity, shift, line='', planned_date=None, batch='', notes=''):
    _need(user, 'add_production')
    require_site(user, site)
    problems = []
    planned_quantity = Decimal(planned_quantity or 0)
    if planned_quantity <= 0:
        problems.append('Planned output must be more than zero.')
    if shift not in ('A', 'B', 'C'):
        problems.append('Choose the shift.')
    batch = (batch or '').strip().upper()
    if batch and Production.objects.filter(batch=batch).exists():
        problems.append(f'Batch {batch} already exists.')
    recipe = current_recipe(product, planned_date)
    if not recipe and Recipe.objects.filter(product=product).exists():
        problems.append(f'{product.name} has no approved version effective for the planned date — approve the appropriate recipe first.')
    if problems:
        raise ValidationError(problems)
    p = Production.objects.create(site=site, product=product, planned_quantity=planned_quantity, shift=shift, line=(line or '')[:40], recipe=recipe,
                                  planned_date=planned_date or timezone.localdate(), batch=batch or next_batch(site, shift, planned_date), notes=notes or '',
                                  created_by=user)
    _log(user, site, 'Process order created', p.batch, f'{product.code} {qtxt(planned_quantity)} {product.unit}' + (f' · recipe v{recipe.version}' if recipe else ' · no recipe'))
    return p


def _lock(pk, user):
    obj = Production.objects.select_for_update().select_related('site', 'product', 'recipe').get(pk=pk)
    require_site(user, obj.site)
    return obj


@transaction.atomic
def issue(user, pk, material, quantity, source='', grn=None):
    _need(user, 'complete_production')
    p = _lock(pk, user)
    if p.status not in ('Planned', 'In progress'):
        raise ValidationError(f'{p.batch} is {p.status.lower()} — material can no longer be issued.')
    quantity = Decimal(str(quantity or 0)).quantize(Decimal('0.001'))
    if quantity <= 0:
        raise ValidationError('Enter the quantity issued.')
    if material.pk == p.product_id:
        raise ValidationError('A batch cannot consume its own product.')
    if grn and (grn.site_id != p.site_id or not grn.lines.filter(product=material).exists()):
        raise ValidationError(f'{grn.number} did not bring {material.name} to {p.site.code}.')
    n = p.issues.count() + 1
    m = move_stock(user, p.site, material, -quantity, f'prd:{p.pk}:issue:{n}', 'Consumption', f'{p.batch} · {source or "store"}')
    i = MaterialIssue.objects.create(production=p, material=material, quantity=quantity, source=(source or '')[:60], grn=grn, unit_cost=m.unit_cost or 0, cost_value=abs(m.value), issued_by=user)
    if p.status == 'Planned':
        p.status, p.started_at = 'In progress', timezone.now()
        p.save(update_fields=['status', 'started_at'])
    _log(user, p.site, 'Material issued', p.batch, f'{material.code} {qtxt(quantity)} {material.unit} @ ₹{inr(i.unit_cost)} · {source}')
    return i


def variance(p, output):
    """Per material: standard, issued, difference and whether it is outside tolerance."""
    issued = {}
    for i in p.issues.select_related('material'):
        issued[i.material] = issued.get(i.material, ZERO) + i.quantity
    rows = []
    for material, (std, tol) in standard(p, output).items():
        got = issued.pop(material, ZERO)
        diff = got - std
        rows.append({'material': material, 'standard': std, 'issued': got, 'diff': diff, 'pct': (diff / std * 100).quantize(Decimal('0.1')) if std else None,
                     'tol': tol, 'out': bool(std) and abs(diff) > std * tol / 100})
    for material, got in issued.items():  # issued but not in the recipe
        rows.append({'material': material, 'standard': ZERO, 'issued': got, 'diff': got, 'pct': None, 'tol': ZERO, 'out': True})
    return rows


@transaction.atomic
def complete(user, pk, output=None, scrap=0, note=''):
    """Record good output and scrap; check material variance; fix the batch's actual cost. Batch goes to quality hold."""
    _need(user, 'complete_production')
    p = _lock(pk, user)
    if p.status not in ('Planned', 'In progress'):
        raise ValidationError('Only planned or running batches can be completed.')
    output = Decimal(output if output is not None else p.actual_quantity)
    scrap = Decimal(scrap or 0)
    note = (note or '').strip()
    if output <= 0:
        raise ValidationError('Planned batch must have a positive actual output.')
    if scrap < 0:
        raise ValidationError('Scrap cannot be negative.')
    if p.recipe_id and not p.issues.exists():
        raise ValidationError('Issue the materials used before completing (FR-PRD-010 material reconciliation).')
    outside = [r for r in variance(p, output + scrap) if r['out']]
    if outside and not note:
        raise ValidationError('Material use is outside tolerance for ' + ', '.join(r['material'].name for r in outside) + ' — explain the variance.')
    cost = sum((i.value for i in p.issues.all()), ZERO)
    p.actual_quantity, p.scrap_quantity, p.variance_note = output, scrap, note[:200]
    p.material_cost = cost
    p.unit_cost = (cost / output).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP) if cost else None
    p.status, p.completed_at, p.completed_by = 'Quality hold', timezone.now(), user
    p.save()
    _log(user, p.site, 'Production complete', p.batch, f'output {qtxt(output)} {p.product.unit}, scrap {qtxt(scrap)} · cost ₹{inr(cost)}' + (f' · {note}' if note else ''))
    return p


@transaction.atomic
def cancel(user, pk, note):
    _need(user, 'add_production')
    p = _lock(pk, user)
    if p.status != 'Planned' or p.issues.exists():
        raise ValidationError('Only planned batches with no material issued can be cancelled.')
    if not (note or '').strip():
        raise ValidationError('Give the reason for cancelling.')
    p.status, p.variance_note = 'Cancelled', note.strip()[:200]
    p.save()
    _log(user, p.site, 'Process order cancelled', p.batch, note)
    return p


# ---------- Shift log ----------

def save_shift_log(user, data):
    _need(user, 'add_shiftlog')
    require_site(user, data['site'])
    problems = []
    if not (data.get('line') or '').strip(): problems.append('Enter the line / mill.')
    if not (data.get('supervisor') or '').strip(): problems.append('Enter the shift supervisor.')
    hours, down = Decimal(data.get('run_hours') or 0), int(data.get('downtime_minutes') or 0)
    if hours < 0 or hours > 12: problems.append('Running hours must be between 0 and 12 for one shift.')
    if down < 0 or down > 720: problems.append('Downtime must be between 0 and 720 minutes.')
    if down and not data.get('downtime_reason'): problems.append('Give the downtime reason.')
    if hours * 60 + down > 12 * 60: problems.append('Running hours + downtime exceed a 12-hour shift.')
    o, c = data.get('meter_opening'), data.get('meter_closing')
    if o is not None and c is not None and c < o: problems.append('Meter closing cannot be less than opening.')
    if ShiftLog.objects.filter(site=data['site'], date=data['date'], shift=data['shift'], line__iexact=(data.get('line') or '').strip()).exists():
        problems.append('This shift is already logged for that line.')
    if problems:
        raise ValidationError(problems)
    log = ShiftLog.objects.create(created_by=user, **{**data, 'line': data['line'].strip()[:40], 'supervisor': data['supervisor'].strip()[:80]})
    _log(user, log.site, 'Shift logged', f'{log.date:%d-%m-%Y} {log.shift} {log.line}', f'{log.run_hours} h run, {log.downtime_minutes} min down, {log.power_kwh} kWh')
    return log


# ---------- Packing ----------

def bag_kg(product):
    return product.bag_weight_kg or Organization.objects.filter(pk=1).values_list('default_bag_weight', flat=True).first() or Decimal('50')


@transaction.atomic
def pack(user, site, data):
    """data: date, shift, packer, bulk_product, bag_product, batch, bulk_issued, bags_packed, bags_burst, spillage_kg, downtime_minutes, empty_bags, difference_reason."""
    _need(user, 'add_packingrun')
    require_site(user, site)
    if data.get('batch'):
        data = {**data, 'batch': Production.objects.select_for_update().get(pk=data['batch'].pk)}
    bulk, bag = data['bulk_product'], data['bag_product']
    problems = []
    if bulk.unit != 'MT': problems.append(f'{bulk.name} must be stocked in MT.')
    if bag.unit != 'BAG': problems.append(f'{bag.name} must be stocked in bags.')
    if data['bags_packed'] <= 0: problems.append('Enter the bags packed.')
    if data.get('batch') and (data['batch'].site_id != site.pk or data['batch'].product_id != bulk.pk):
        problems.append('Choose a bulk batch of this product at this plant.')
    if data.get('batch') and data['batch'].status != 'Released': problems.append(f'Batch {data["batch"].batch} is not released by quality.')
    if problems:
        raise ValidationError(problems)
    theoretical = Decimal(data['bags_packed']) * Decimal(bag_kg(bag)) / 1000
    diff = (Decimal(data['bulk_issued']) - theoretical - Decimal(data.get('spillage_kg') or 0) / 1000).quantize(Decimal('0.001'))
    reason = (data.get('difference_reason') or '').strip()
    if abs(diff) > Decimal(data['bulk_issued']) * PACK_TOLERANCE / 100 and not reason:
        raise ValidationError(f'Bulk issued differs from bags × {qtxt(bag_kg(bag))} kg + spillage by {qtxt(diff)} MT — record the reason (meter, bag weight check…).')
    run = PackingRun.objects.create(number=next_number('PCK', site), site=site, created_by=user, difference_mt=diff, difference_reason=reason[:200],
                                    **{k: data.get(k) for k in ('date', 'shift', 'packer', 'bulk_product', 'bag_product', 'batch', 'bulk_issued', 'bags_packed',
                                                                'bags_burst', 'spillage_kg', 'downtime_minutes', 'empty_bags')})
    out = move_stock(user, site, bulk, -Decimal(data['bulk_issued']), f'pck:{run.pk}:bulk', 'Consumption', f'{run.number} · {run.packer}')
    cost = abs(out.value)
    if data.get('empty_bags'):
        e = move_stock(user, site, data['empty_bags'], -Decimal(data['bags_packed'] + (data.get('bags_burst') or 0)), f'pck:{run.pk}:bags', 'Consumption', run.number)
        cost += abs(e.value)
    run.unit_cost = (cost / data['bags_packed']).quantize(Decimal('0.0001')) if cost else ZERO
    run.save(update_fields=['unit_cost'])
    move_stock(user, site, bag, Decimal(data['bags_packed']), f'pck:{run.pk}:out', 'Production', f'{run.number}' + (f' · batch {data["batch"].batch}' if data.get('batch') else ''),
               unit_cost=run.unit_cost or None, value=cost)
    _log(user, site, 'Packing recorded', run.number, f'{data["bags_packed"]} bags, {data.get("bags_burst") or 0} burst, bulk {qtxt(data["bulk_issued"])} MT, difference {qtxt(diff)} MT')
    return run
