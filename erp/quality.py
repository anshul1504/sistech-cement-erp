"""Quality lab (SRS §5.7, FR-QC-001…006) and batch traceability (FR-INV-010).

Controls enforced here (screens and the future API both call these functions):
- results are checked against the product's specification automatically and are never edited; a retest needs a reason and
  supersedes the earlier value, which stays visible (FR-QC-003);
- equipment with overdue calibration cannot be used for a result (FR-QC-006);
- a batch is released only by an authorised quality user who neither created nor completed it, and only when every
  specification required for release has a current passing result (FR-QC-004); an out-of-spec batch can be released only
  'under deviation' with a recorded justification, or rejected (FR-QC-003);
- a failing result recorded after release (e.g. 28-day strength) raises an audit alert for investigation;
- released output enters stock once at the batch's actual cost.
"""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone

from .gst import qtxt
from .models import Production, QualitySpec, QualityResult, LabEquipment, AuditEvent, MaterialIssue, DispatchLine
from .services import move_stock, require_site


def _need(user, perm):
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def _log(user, site, action, reference, detail=''):
    AuditEvent.objects.create(actor=user, site=site, action=action, reference=str(reference), detail=detail[:250])


def current_results(production):
    return {r.spec_id: r for r in production.results.filter(superseded_by__isnull=True).select_related('spec', 'equipment', 'analyst')}


def evidence(production):
    """Per active spec: current result (if any), pass/fail, and what is still missing for release."""
    specs = list(QualitySpec.objects.filter(product=production.product, active=True))
    cur = current_results(production)
    rows = [{'spec': s, 'result': cur.get(s.pk), 'due': production.completed_at and (production.completed_at.date() + timedelta(days=s.age_days))}
            for s in specs]
    missing = [r['spec'] for r in rows if r['spec'].required_for_release and not r['result']]
    failed = [r for r in rows if r['result'] and not r['result'].ok]
    return {'rows': rows, 'missing': missing, 'failed': failed, 'has_specs': bool(specs)}


@transaction.atomic
def record_result(user, production_pk, spec, value, equipment=None, sample_ref='', remarks='', retest_reason=''):
    _need(user, 'add_qualitytest')
    p = Production.objects.select_for_update().select_related('site', 'product').get(pk=production_pk)
    require_site(user, p.site)
    if p.status in ('Planned', 'Cancelled', 'Rejected'):
        raise ValidationError(f'{p.batch} is {p.status.lower()} — results cannot be recorded.')
    if spec.product_id != p.product_id or not spec.active:
        raise ValidationError('This parameter is not part of the product specification.')
    try:
        value = Decimal(str(value))
    except Exception:
        raise ValidationError('Enter the measured value as a number.')
    if not value.is_finite():
        raise ValidationError('Measured value must be a finite number.')
    if equipment:
        if equipment.site_id != p.site_id or not equipment.active:
            raise ValidationError(f'{equipment} is not active equipment at {p.site.code}.')
        if equipment.overdue:
            raise ValidationError(f'{equipment} calibration was due on {equipment.calibration_due:%d %b %Y} — calibrate it before use.')
    current = p.results.filter(spec=spec, superseded_by__isnull=True).first()
    retest_reason = (retest_reason or '').strip()
    if current and not retest_reason:
        raise ValidationError(f'{spec.parameter} already has a result ({qtxt(current.value)}). A retest needs a reason; the first result stays on record.')
    r = QualityResult.objects.create(production=p, spec=spec, value=value, ok=spec.passes(value), equipment=equipment, sample_ref=(sample_ref or '')[:40],
                                     remarks=(remarks or '')[:150], retest_reason=retest_reason[:150], analyst=user)
    if current:
        current.superseded_by = r
        current.save(update_fields=['superseded_by'])
    _log(user, p.site, 'Quality result', p.batch, f'{spec.parameter} {qtxt(value)} {spec.unit} → {"pass" if r.ok else "OUT OF SPEC"} ({spec.limit_text})'
         + (f' · retest: {retest_reason}' if current else ''))
    if not r.ok and p.status == 'Released':
        _log(user, p.site, 'Quality alert', p.batch, f'Released batch failed {spec.parameter}: {qtxt(value)} vs {spec.limit_text}. Investigate dispatched quantity.')
    return r


@transaction.atomic
def release(user, pk, decision='release', note=''):
    """decision: 'release' (all required results pass), 'deviation' (out of spec, justified) or 'reject'."""
    _need(user, 'release_production')
    p = Production.objects.select_for_update().select_related('site', 'product').get(pk=pk)
    require_site(user, p.site)
    note = (note or '').strip()
    if p.status != 'Quality hold':
        raise ValidationError('Batch must be on quality hold.')
    if user.pk in (p.created_by_id, p.completed_by_id):
        raise ValidationError('A different quality user must release the batch.')
    ev = evidence(p)
    if decision == 'reject':
        if not note:
            raise ValidationError('Record why the batch is rejected.')
        p.status, p.disposition, p.disposition_note, p.released_by, p.released_at = 'Rejected', 'reject', note[:200], user, timezone.now()
        p.save()
        _log(user, p.site, 'Batch rejected', p.batch, note)
        return p
    if ev['has_specs']:
        if ev['missing']:
            raise ValidationError('Results still needed before release: ' + ', '.join(s.parameter for s in ev['missing']) + '.')
        if ev['failed'] and decision != 'deviation':
            raise ValidationError('Out of specification: ' + ', '.join(f'{r["spec"].parameter} {qtxt(r["result"].value)}' for r in ev['failed']) +
                                  '. Reject the batch, retest with a reason, or release under deviation with justification.')
        if decision == 'deviation' and not ev['failed']:
            raise ValidationError('Nothing is out of specification — release normally.')
    else:
        if not p.tests.exists() or p.tests.filter(disposition='Fail').exists():
            raise ValidationError('At least one passing test and no failing tests are required.')
        if decision == 'deviation':
            raise ValidationError('Release under deviation needs specification results.')
    if decision == 'deviation' and not note:
        raise ValidationError('Release under deviation needs a justification (e.g. customer approval, end use).')
    move_stock(user, p.site, p.product, p.actual_quantity, f'production:{p.pk}', 'Production', p.batch, unit_cost=p.unit_cost, value=p.material_cost)
    p.status, p.disposition, p.disposition_note, p.released_by, p.released_at = 'Released', decision, note[:200], user, timezone.now()
    p.save()
    _log(user, p.site, 'Production release' if decision == 'release' else 'Released under deviation', p.batch,
         f'{qtxt(p.actual_quantity)} {p.product.unit} to stock' + (f' @ ₹{p.unit_cost}' if p.unit_cost else '') + (f' · {note}' if note else ''))
    return p


@transaction.atomic
def save_equipment(user, site, code, name, due, last=None, certificate='', pk=None):
    _need(user, 'add_labequipment')
    require_site(user, site)
    code = (code or '').strip().upper()
    if not code or not (name or '').strip() or not due:
        raise ValidationError('Enter code, name and calibration due date.')
    if last and due < last:
        raise ValidationError('Due date cannot be before the last calibration.')
    if LabEquipment.objects.filter(site=site, code=code).exclude(pk=pk).exists():
        raise ValidationError(f'{code} already exists at {site.code}.')
    eq = LabEquipment.objects.get(pk=pk) if pk else LabEquipment(site=site)
    if pk:
        require_site(user, eq.site)
    eq.code, eq.name, eq.calibration_due, eq.last_calibrated, eq.certificate, eq.site = code[:30], name.strip()[:100], due, last, (certificate or '')[:60], site
    eq.save()
    _log(user, site, 'Lab equipment calibration' if pk else 'Lab equipment added', eq.code, f'due {due:%d %b %Y}' + (f' · cert {certificate}' if certificate else ''))
    return eq


# ---------- Traceability (FR-INV-010) ----------

def trace(production):
    """Batch → raw-material lots (GRNs) and issues → QC results → packing → dispatches → invoices → customers."""
    issues = MaterialIssue.objects.filter(production=production).select_related('material', 'grn__supplier')
    dl = DispatchLine.objects.filter(batch=production).select_related('dispatch__order__partner', 'dispatch__vehicle', 'order_line__product')
    shipments = []
    for l in dl:
        d = l.dispatch
        inv = getattr(d, 'invoice', None)
        shipments.append({'dispatch': d, 'qty': l.quantity, 'unit': l.order_line.product.unit, 'customer': d.order.partner, 'invoice': inv})
    return {'issues': issues, 'results': production.results.select_related('spec', 'analyst', 'equipment').order_by('spec__sequence', 'tested_at'),
            'packing': production.packing_runs.select_related('bag_product'), 'shipments': shipments,
            'customers': sorted({s['customer'].name for s in shipments})}
