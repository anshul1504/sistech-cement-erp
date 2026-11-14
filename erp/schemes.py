"""Approved immutable scheme versions and retained, non-posting incentive previews."""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from .crm import sites_for
from .gst import money
from .models import (IncentiveScheme, SchemeVersion, SchemeSlab, SchemeEvaluation, Site, Invoice,
                     CreditNote, AuditEvent)
from .scheme_forms import SchemeForm, VersionForm, SlabForm
from .services import permitted, require_site

ZERO = Decimal('0')
RULE_FIELDS = ['starts_on', 'ends_on', 'product', 'partner', 'customer_kind', 'district', 'territory', 'basis', 'dealer_cap', 'notes']


def scope(qs, user, prefix=''):
    if user.is_superuser:
        return qs
    return qs.filter(**{(prefix + '__' if prefix else '') + 'site__in': sites_for(user)})


def _scheme(user, pk):
    obj = IncentiveScheme.objects.select_for_update().select_related('site').get(pk=pk)
    require_site(user, obj.site)
    return obj


def _version(user, pk):
    # Lock the shared family before a version, including new-version allocation.
    header = SchemeVersion.objects.only('scheme_id').get(pk=pk)
    scheme = _scheme(user, header.scheme_id)
    obj = SchemeVersion.objects.select_for_update().get(pk=pk)
    obj.scheme = scheme
    return obj


def _audit(user, scheme, action, detail):
    AuditEvent.objects.create(actor=user, site=scheme.site, action=action, reference=f'scheme:{scheme.pk}', detail=detail)


def _normalized(value):
    return ' '.join((value or '').split()).casefold()


def _errors(form):
    return ValidationError([f'{key}: {m}' for key, values in form.errors.items() for m in values])


def _validate(version, slabs):
    if version.ends_on < version.starts_on:
        raise ValidationError('The end date cannot precede the start date.')
    if version.scheme.versions.filter(status='approved', starts_on__lte=version.ends_on, ends_on__gte=version.starts_on).exclude(pk=version.pk).exists():
        raise ValidationError('Dates overlap an approved version of this scheme. Use a later non-overlapping period.')
    previous = version.scheme.versions.filter(status='approved').exclude(pk=version.pk).order_by('-ends_on').first()
    if previous and version.starts_on <= previous.ends_on:
        raise ValidationError('A revision must start after the last approved version ends.')
    if version.partner and version.customer_kind and version.partner.kind != version.customer_kind:
        raise ValidationError('The selected customer does not match the customer-type scope.')
    if version.partner and version.district and _normalized(version.partner.district) != _normalized(version.district):
        raise ValidationError('The selected customer does not match the district scope.')
    if version.partner and version.territory and _normalized(version.partner.territory) != _normalized(version.territory):
        raise ValidationError('The selected customer does not match the territory scope.')
    if not slabs or len(slabs) > 20:
        raise ValidationError('Provide between one and twenty sales slabs.')
    thresholds = set()
    for row in slabs:
        check = SlabForm(row)
        if not check.is_valid():
            raise _errors(check)
        threshold = check.cleaned_data['minimum_sales']
        if threshold in thresholds:
            raise ValidationError('Each slab must have a different minimum sales amount.')
        thresholds.add(threshold)


@transaction.atomic
def save_rules(user, header, rules, slabs, pk=None):
    permitted(user, 'change_schemeversion' if pk else 'add_incentivescheme')
    if pk:
        version = _version(user, pk)
        scheme = version.scheme
        if version.status != 'draft':
            raise ValidationError('Submitted and decided rules are locked. Prepare a new version instead.')
    else:
        form = SchemeForm(header, user=user)
        if not form.is_valid():
            raise _errors(form)
        site = form.cleaned_data['site']
        require_site(user, site)
        Site.objects.select_for_update().get(pk=site.pk)
        scheme = form.save(commit=False)
        scheme.created_by = user
        scheme.full_clean()
        scheme.save()
        version = SchemeVersion(scheme=scheme, created_by=user, updated_by=user)
    form = VersionForm(rules)
    if not form.is_valid():
        raise _errors(form)
    for name in RULE_FIELDS:
        setattr(version, name, form.cleaned_data[name])
    version.district = ' '.join(version.district.split())
    version.territory = ' '.join(version.territory.split())
    _validate(version, slabs)
    version.updated_by = user
    version.full_clean()
    version.save()
    version.contributors.add(user)
    version.slabs.all().delete()
    for row in slabs:
        check = SlabForm(row); check.is_valid()
        SchemeSlab.objects.create(version=version, **check.cleaned_data)
    _audit(user, scheme, 'Scheme rules saved', json.dumps(rule_snapshot(version)))
    return version


@transaction.atomic
def revise(user, scheme_pk):
    permitted(user, 'add_incentivescheme')
    scheme = _scheme(user, scheme_pk)
    if scheme.versions.filter(status__in=['draft', 'submitted']).exists():
        raise ValidationError('This scheme already has a draft or submitted version.')
    latest = scheme.versions.order_by('-revision').first()
    if latest is None:
        raise ValidationError('No source version to revise.')
    values = {name: getattr(latest, name) for name in RULE_FIELDS}
    last_approved = scheme.versions.filter(status='approved').order_by('-ends_on').first()
    if last_approved:
        values['starts_on'] = last_approved.ends_on + timedelta(days=1)
        values['ends_on'] = values['starts_on'] + (latest.ends_on - latest.starts_on)
    draft = SchemeVersion.objects.create(scheme=scheme, revision=latest.revision+1, created_by=user, updated_by=user, **values)
    draft.contributors.add(user)
    SchemeSlab.objects.bulk_create([SchemeSlab(version=draft, minimum_sales=s.minimum_sales, percentage=s.percentage) for s in latest.slabs.all()])
    _audit(user, scheme, 'Scheme revision prepared', f'v{draft.revision} from v{latest.revision}; source version unchanged')
    return draft


def rule_snapshot(version):
    return dict(scheme=version.scheme.code, name=version.scheme.name, site=version.scheme.site_id, site_code=version.scheme.site.code,
        revision=version.revision, starts_on=str(version.starts_on), ends_on=str(version.ends_on),
        product=version.product_id, product_name=str(version.product) if version.product_id else 'All products',
        partner=version.partner_id, partner_name=str(version.partner) if version.partner_id else 'All customers',
        customer_kind=version.customer_kind, district=version.district, territory=version.territory, basis=version.basis,
        dealer_cap=str(version.dealer_cap) if version.dealer_cap is not None else None,
        slabs=[dict(minimum_sales=str(s.minimum_sales), percentage=str(s.percentage)) for s in version.slabs.all()], notes=version.notes)


@transaction.atomic
def submit(user, pk, note):
    permitted(user, 'submit_schemeversion')
    version = _version(user, pk)
    if version.status != 'draft' or not (note or '').strip():
        raise ValidationError('Submit a draft and record the reason/evidence for the proposed rules.')
    _validate(version, list(version.slabs.values('minimum_sales', 'percentage')))
    version.status, version.submitted_by = 'submitted', user
    version.save()
    version.contributors.add(user)
    _audit(user, version.scheme, 'Scheme submitted', json.dumps(dict(note=note, rules=rule_snapshot(version))))
    return version


@transaction.atomic
def decide(user, pk, approve, note):
    permitted(user, 'approve_schemeversion')
    version = _version(user, pk)
    if version.status != 'submitted':
        raise ValidationError('Only a submitted version can be reviewed.')
    if user.pk in [version.created_by_id, version.updated_by_id, version.submitted_by_id] or version.contributors.filter(pk=user.pk).exists():
        raise ValidationError('A contributor cannot approve or reject their own scheme rules.')
    if not (note or '').strip():
        raise ValidationError('Record the independent review finding.')
    if approve:
        _validate(version, list(version.slabs.values('minimum_sales', 'percentage')))
    version.status = 'approved' if approve else 'rejected'
    version.decided_by, version.decided_at, version.decision_note = user, timezone.now(), note.strip()
    version.save()
    _audit(user, version.scheme, 'Scheme approved' if approve else 'Scheme rejected', json.dumps(dict(note=note, rules=rule_snapshot(version))))
    return version


def _payload(version, starts_on, ends_on):
    """Current source state, frozen on calculation. Never writes accounting or settlements."""
    today = timezone.localdate()
    if version.status != 'approved':
        raise ValidationError('Calculations require independently approved scheme rules.')
    if starts_on > ends_on or starts_on < version.starts_on or ends_on > version.ends_on or ends_on > today:
        raise ValidationError('Choose a non-future invoice period inside the approved version dates.')
    invoices = Invoice.objects.filter(site=version.scheme.site, date__range=(starts_on, ends_on)).select_related('partner').prefetch_related(
        'lines', 'credit_notes__lines', 'settlements__receipt', 'settlements__credit_note').order_by('partner_id', 'pk')
    if version.partner_id:
        invoices = invoices.filter(partner_id=version.partner_id)
    dealers = {}
    for inv in invoices:
        p = inv.partner
        if version.customer_kind and p.kind != version.customer_kind:
            continue
        if version.district and _normalized(p.district) != _normalized(version.district):
            continue
        if version.territory and _normalized(p.territory) != _normalized(version.territory):
            continue
        selected = [l for l in inv.lines.all() if not version.product_id or l.product_id == version.product_id]
        if version.product_id and not selected:
            continue
        taxable = sum((l.taxable for l in selected), ZERO) if version.product_id else inv.taxable
        ids = {l.pk for l in selected}
        credits, credited = [], ZERO
        for cn in sorted(inv.credit_notes.all(), key=lambda item: item.pk):
            if cn.date > today:
                continue
            lines = sorted(cn.lines.all(), key=lambda item: item.pk)
            if version.product_id and not lines and cn.taxable:
                raise ValidationError(f'{cn.number} has no line allocation. Product-scoped incentive cannot be calculated safely.')
            amount = sum((l.taxable for l in lines if l.invoice_line_id in ids), ZERO) if version.product_id else cn.taxable
            credited += amount
            credits.append(dict(id=cn.pk, number=cn.number, date=str(cn.date), taxable=str(amount),
                                lines=[dict(invoice_line=l.invoice_line_id, taxable=str(l.taxable)) for l in lines]))
        net = money(max(taxable - credited, ZERO))
        allocations, settled = [], ZERO
        for s in sorted(inv.settlements.all(), key=lambda item: item.pk):
            if s.receipt_id:
                receipt = s.receipt
                valid = receipt.status == 'posted' and receipt.date <= today
                allocations.append(dict(id=s.pk, source='receipt', source_id=receipt.pk, number=receipt.number, date=str(receipt.date), status=receipt.status, amount=str(s.amount)))
            else:
                cn = s.credit_note
                valid = cn is not None and cn.date <= today
                allocations.append(dict(id=s.pk, source='credit_note', source_id=s.credit_note_id, number=cn.number if cn else '', date=str(cn.date) if cn else '', amount=str(s.amount)))
            if valid: settled += s.amount
        outstanding = money(max(inv.grand_total - settled, ZERO))
        eligible = version.basis == 'net_sales' or outstanding <= ZERO
        base = net if eligible else ZERO
        row = dealers.setdefault(p.pk, dict(partner_id=p.pk, code=p.code, name=p.name, kind=p.kind, district=p.district, territory=p.territory,
                                            taxable=ZERO, eligible=ZERO, invoices=[]))
        row['taxable'] += net; row['eligible'] += base
        row['invoices'].append(dict(id=inv.pk, number=inv.number, date=str(inv.date), buyer=inv.buyer_name,
            taxable=str(taxable), credits=str(credited), net=str(net), outstanding=str(outstanding), eligible=str(base),
            reason='Eligible' if eligible else 'Invoice not fully settled',
            lines=[dict(id=l.pk, product=l.product_id, description=l.description, taxable=str(l.taxable)) for l in selected],
            credit_notes=credits, allocations=allocations))
    slabs = list(version.slabs.all())
    rows, total = [], ZERO
    for row in dealers.values():
        slab = next((s for s in reversed(slabs) if row['eligible'] >= s.minimum_sales), None)
        rate = slab.percentage if slab else ZERO
        reward = money(row['eligible'] * rate / 100)
        if version.dealer_cap is not None:
            reward = min(reward, version.dealer_cap)
        total += reward
        rows.append(dict(**{k: v for k, v in row.items() if k not in ['taxable', 'eligible']},
                         taxable=str(money(row['taxable'])), eligible=str(money(row['eligible'])), percentage=str(rate),
                         threshold=str(slab.minimum_sales) if slab else None, incentive=str(reward)))
    payload = dict(engine='dealer-taxable-v1', rules=rule_snapshot(version), starts_on=str(starts_on), ends_on=str(ends_on), dealers=rows, total=str(money(total)))
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload, digest


@transaction.atomic
def calculate(user, pk, starts_on, ends_on, submission_key):
    permitted(user, 'add_schemeevaluation')
    version = _version(user, pk)
    try:
        key = UUID(str(submission_key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Open a fresh calculation form.')
    # Global key is serialized by the site row as well as its unique constraint.
    Site.objects.select_for_update().get(pk=version.scheme.site_id)
    existing = SchemeEvaluation.objects.filter(submission_key=key).first()
    if existing:
        if (existing.version_id, existing.starts_on, existing.ends_on, existing.created_by_id) != (pk, starts_on, ends_on, user.pk):
            raise ValidationError('This form was already used for a different calculation. Open a fresh form.')
        return existing
    payload, digest = _payload(version, starts_on, ends_on)
    payload['observed_on'] = str(timezone.localdate())
    obj = SchemeEvaluation(version=version, starts_on=starts_on, ends_on=ends_on, submission_key=key,
        snapshot=payload, source_digest=digest, total=Decimal(payload['total']), created_by=user)
    obj.full_clean(validate_unique=False)
    try:
        with transaction.atomic():
            obj.save()
    except IntegrityError:
        # A UUID deliberately reused across sites can race on PostgreSQL.
        existing = SchemeEvaluation.objects.filter(submission_key=key).first()
        if existing and (existing.version_id, existing.starts_on, existing.ends_on, existing.created_by_id) == (pk, starts_on, ends_on, user.pk):
            return existing
        raise ValidationError('This calculation form has already been used. Open a fresh form.')
    _audit(user, version.scheme, 'Scheme calculated', f'{obj} · {starts_on} to {ends_on} · {obj.total} · source {digest}; preview only, no posting')
    return obj


@transaction.atomic
def check_sources(user, pk):
    permitted(user, 'add_schemeevaluation')
    evaluation = SchemeEvaluation.objects.select_related('version__scheme').get(pk=pk)
    version = _version(user, evaluation.version_id)
    _, digest = _payload(version, evaluation.starts_on, evaluation.ends_on)
    current = digest == evaluation.source_digest
    _audit(user, version.scheme, 'Scheme sources checked', f'{evaluation} · {"unchanged" if current else "changed; recalculate before use"}')
    return current
