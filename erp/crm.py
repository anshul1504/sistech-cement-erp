"""Site/owner scoped CRM and evidence-based dealer activation/amendments."""
import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum, Max
from django.utils import timezone

from .forms import normalise_mobile
from .gst import next_number
from .models import (CRMPolicy, CRMLead, CRMActivity, DealerApplication, DealerDocument, DealerReview, SalesTarget, Partner, PartnerDocument,
                     Site, SiteAccess, AuditEvent, CUSTOMER_KINDS, LEAD_STAGES, LEAD_SOURCES, PARTNER_DOCUMENT_TYPES, Invoice, CreditNote, Settlement)
from .services import permitted, require_site
from .crm_forms import DealerProfileForm, DealerUploadForm, PROFILE_FIELDS, PROTECTED_CUSTOMER_FIELDS

ZERO = Decimal('0')
OPEN_APPLICATIONS = ['draft', 'submitted', 'changes', 'kyc', 'credit']
MAKER_ACTIONS = ['Created', 'Profile updated', 'Document uploaded', 'Document withdrawn', 'Submitted', 'Owner reassigned']


def sites_for(user):
    return Site.objects.all() if user.is_superuser else Site.objects.filter(siteaccess__user=user)


def owners_for(user):
    from accounts.security import access_allowed
    ids = [u.pk for u in get_user_model().objects.filter(is_active=True) if access_allowed(u) and u.has_perm('erp.view_crmlead')]
    return get_user_model().objects.filter(pk__in=ids).order_by('first_name', 'username')


def lead_scope(qs, user, prefix=''):
    if user.is_superuser:
        return qs
    root = prefix + '__' if prefix else ''
    qs = qs.filter(**{root + 'site__siteaccess__user': user})
    return qs if user.has_perm('erp.view_team_crm') else qs.filter(**{root + 'owner': user})


def _lead(user, pk):
    lead = CRMLead.objects.select_for_update().select_related('site', 'owner', 'partner').get(pk=pk)
    require_site(user, lead.site)
    if lead.owner_id != user.pk and not (user.is_superuser or user.has_perm('erp.view_team_crm')):
        raise PermissionDenied
    return lead


def _lock():
    CRMPolicy.objects.get_or_create(pk=1)
    return CRMPolicy.objects.select_for_update().get(pk=1)


def _application(user, pk, permission):
    permitted(user, permission)
    _lock()
    app = DealerApplication.objects.select_for_update().select_related('lead', 'site', 'partner').get(pk=pk)
    app.lead = _lead(user, app.lead_id)
    return app


def _audit(user, lead, action, detail=''):
    AuditEvent.objects.create(actor=user, site=lead.site, action=action, reference=lead.number, detail=detail)


def _owner(user, owner, site):
    from accounts.security import access_allowed
    if not owner or not access_allowed(owner) or not owner.has_perm('erp.view_crmlead'):
        raise ValidationError('Choose an active CRM user as the owner.')
    require_site(owner, site)
    if owner.pk != user.pk and not user.has_perm('erp.assign_crmlead'):
        raise PermissionDenied


@transaction.atomic
def save_lead(user, data, pk=None):
    permitted(user, 'change_crmlead' if pk else 'add_crmlead')
    site = data['site']
    require_site(user, site)
    # Match the onboarding decision lock order before acquiring the lead row.
    _lock()
    applications = list(DealerApplication.objects.select_for_update().filter(lead_id=pk, status__in=OPEN_APPLICATIONS)) if pk else []
    lead = _lead(user, pk) if pk else CRMLead(site=site, created_by=user)
    former_owner = lead.owner_id
    owner_changed = bool(pk and former_owner != (data.get('owner') or user).pk)
    reason = (data.get('owner_change_reason') or '').strip()
    if owner_changed:
        permitted(user, 'assign_crmlead')
        if not reason or len(reason) > 1000:
            raise ValidationError('Record an ownership change reason (up to 1000 characters).')
    if pk and lead.site_id != site.pk:
        raise ValidationError('The originating site cannot be changed.')
    if pk and lead.stage in ('won', 'lost'):
        raise ValidationError('Closed leads cannot be edited; reopen a lost lead or create a reviewed amendment for a converted dealer.')
    if not site.active:
        raise ValidationError('Choose an active plant or depot.')
    owner = data.get('owner') or user
    _owner(user, owner, site)
    mobile = normalise_mobile(data.get('mobile'))
    if not mobile:
        raise ValidationError('Enter a valid 10-digit mobile number.')
    # The site row serializes normalized duplicate checks on row-locking databases.
    Site.objects.select_for_update().get(pk=site.pk)
    if CRMLead.objects.filter(site=site, mobile=mobile).exclude(pk=pk).exists():
        raise ValidationError('This mobile already has a lead at this site. Reuse the existing lead.')
    for field in ['business_name', 'contact_name', 'email', 'kind', 'source', 'district', 'territory', 'next_follow_up', 'estimated_value', 'notes']:
        if field in data:
            setattr(lead, field, data[field])
    lead.mobile, lead.owner = mobile, owner
    lead.business_name = lead.business_name.strip()
    if lead.next_follow_up and lead.next_follow_up < timezone.localdate():
        raise ValidationError('A new follow-up cannot be in the past.')
    if not pk:
        lead.number = next_number('LEAD', site)
    lead.full_clean()
    lead.save()
    if owner_changed:
        for app in applications:
            app.lead = lead
            before = dict(status=app.status, submission=app.submission_snapshot, kyc_by=app.kyc_by_id, credit_by=app.credit_by_id,
                          approved_by=app.approved_by_id, approved_credit=str(app.approved_credit) if app.approved_credit is not None else None,
                          approved_terms_days=app.approved_terms_days)
            if app.status in ('submitted', 'kyc', 'credit'):
                app.status = 'changes'
                app.kyc_by = app.credit_by = app.approved_by = None
                app.approved_credit = app.approved_terms_days = app.approved_at = None
                app.save()
            _review(app, user, 'Owner reassigned', reason, {**_snapshot(app, app.submission_snapshot.get('policy', {})),
                    'ownership': dict(previous=former_owner, recipient=owner.pk), 'before_handoff': before})
        _audit(user, lead, 'CRM ownership reassigned', f'{former_owner} → {owner.pk} · {reason}')
    _audit(user, lead, 'CRM lead updated' if pk else 'CRM lead created', f'{lead.business_name} · owner {owner.get_username()}')
    return lead


@transaction.atomic
def change_stage(user, pk, stage, reason=''):
    permitted(user, 'change_crmlead')
    lead = _lead(user, pk)
    reason = (reason or '').strip()
    if stage not in dict(LEAD_STAGES) or stage == 'won':
        raise ValidationError('Conversion requires an approved dealer application or linking an existing active customer.')
    if lead.stage == 'won':
        raise ValidationError('Converted leads retain their conversion history.')
    if lead.applications.filter(status__in=OPEN_APPLICATIONS).exists():
        raise ValidationError('Finish or withdraw the open onboarding review before changing the lead stage.')
    if (stage == 'lost' or lead.stage == 'lost') and not reason:
        raise ValidationError('Record the reason for loss or reopening.')
    lead.stage, lead.closed_reason = stage, reason if stage == 'lost' else ''
    if stage == 'lost':
        lead.next_follow_up = None
    lead.save()
    _audit(user, lead, 'CRM stage changed', f'{stage} · {reason}')
    return lead


@transaction.atomic
def record_activity(user, pk, data):
    permitted(user, 'add_crmactivity')
    lead = _lead(user, pk)
    if lead.stage in ('won', 'lost'):
        raise ValidationError('Record follow-ups on an open lead.')
    happened, follow = data.get('happened_on') or timezone.localdate(), data.get('next_follow_up')
    if happened > timezone.localdate() or (follow and follow < timezone.localdate()):
        raise ValidationError('Activity cannot be future-dated; the next follow-up must be today or later.')
    activity = CRMActivity(lead=lead, created_by=user, **{k: data.get(k) for k in ['kind', 'outcome', 'note']}, happened_on=happened, next_follow_up=follow)
    activity.full_clean()
    activity.save()
    lead.next_follow_up = follow
    if lead.stage == 'new':
        lead.stage = 'contacted'
    lead.save()
    _audit(user, lead, 'CRM activity recorded', f'{activity.get_kind_display()} · {activity.outcome}')
    return activity


@transaction.atomic
def link_existing(user, pk, partner, reason):
    permitted(user, 'change_crmlead')
    permitted(user, 'view_partner')
    _lock()
    lead = _lead(user, pk)
    partner = Partner.objects.select_for_update().get(pk=partner.pk)
    if lead.stage in ('won', 'lost') or lead.applications.filter(status__in=OPEN_APPLICATIONS).exists():
        raise ValidationError('Use an open lead with no pending onboarding review.')
    if not partner.active or partner.kind not in CUSTOMER_KINDS or not (reason or '').strip():
        raise ValidationError('Choose an existing active customer and record the matching evidence.')
    lead.partner, lead.stage, lead.next_follow_up = partner, 'won', None
    lead.save()
    _audit(user, lead, 'CRM existing customer linked', f'{partner.code} · {reason}')
    return lead


def _json(data):
    return {k: str(v) if isinstance(v, Decimal) else v for k, v in data.items()}


def partner_fingerprint(partner):
    data = {name: getattr(partner, name) for name in PROTECTED_CUSTOMER_FIELDS}
    data['active'] = partner.active
    data['documents'] = [dict(type=d.doc_type, number=d.number, expires=str(d.valid_until) if d.valid_until else None,
                              digest=hashlib.sha256(bytes(d.file)).hexdigest()) for d in partner.documents.order_by('pk')]
    return hashlib.sha256(json.dumps(_json(data), sort_keys=True).encode()).hexdigest()


def current_documents(app):
    return app.documents.filter(is_current=True).defer('file').order_by('doc_type')


def _snapshot(app, policy):
    docs = []
    for doc in app.documents.filter(is_current=True).order_by('doc_type'):
        docs.append(dict(id=doc.pk, type=doc.doc_type, version=doc.version, digest=hashlib.sha256(bytes(doc.file)).hexdigest(),
                         number=doc.number, expires=str(doc.valid_until) if doc.valid_until else None))
    return dict(profile=app.profile, owner=app.lead.owner_id, documents=docs, policy=policy, base=app.base_partner_fingerprint)


def _review(app, user, action, note, snapshot=None):
    evidence = {**(snapshot or _snapshot(app, app.submission_snapshot.get('policy', {}))), 'decision': dict(status=app.status, approved_credit=str(app.approved_credit) if app.approved_credit is not None else None, terms=app.approved_terms_days, kyc_by=app.kyc_by_id, credit_by=app.credit_by_id, approved_by=app.approved_by_id, partner=app.partner_id)}
    DealerReview.objects.create(application=app, revision=app.revision, actor=user, action=action, note=note, snapshot=evidence)
    _audit(user, app.lead, 'Dealer ' + action.lower(), f'{app.number} · {note}')


def _editable(app):
    if app.status not in ('draft', 'changes'):
        raise ValidationError('Profile and evidence are locked while under review or after a decision.')


@transaction.atomic
def start_application(user, lead_pk):
    permitted(user, 'add_dealerapplication')
    _lock()
    lead = _lead(user, lead_pk)
    if lead.stage == 'lost':
        raise ValidationError('Reopen this lead before preparing onboarding.')
    if lead.applications.filter(status__in=OPEN_APPLICATIONS).exists():
        raise ValidationError('This lead already has an open application.')
    partner = lead.partner
    profile = {name: str(getattr(partner, name)) if isinstance(getattr(partner, name), Decimal) else getattr(partner, name) for name in PROFILE_FIELDS} if partner else dict(
        kind=lead.kind, name=lead.business_name, contact_person=lead.contact_name, phone=lead.mobile, email=lead.email, district=lead.district,
        territory=lead.territory, credit_limit='0', payment_terms_days=0, security_deposit='0', state='', notes='')
    app = DealerApplication.objects.create(number=next_number('ONB', lead.site), lead=lead, site=lead.site, partner=partner, profile=profile,
        base_partner_fingerprint=partner_fingerprint(partner) if partner else '', created_by=user, updated_by=user)
    if partner:
        for doc in partner.documents.all().order_by('uploaded_at', 'pk'):
            previous = app.documents.filter(doc_type=doc.doc_type)
            version = (previous.aggregate(v=Max('version'))['v'] or 0) + 1
            previous.update(is_current=False)
            app.documents.create(doc_type=doc.doc_type, version=version, number=doc.number, valid_until=doc.valid_until, file=bytes(doc.file),
                file_name=doc.file_name, content_type=doc.content_type, size=doc.size, digest=hashlib.sha256(bytes(doc.file)).hexdigest(), uploaded_by=user)
    _review(app, user, 'Created', 'Amendment of ' + partner.code if partner else 'New customer application')
    return app


@transaction.atomic
def save_application(user, pk, profile):
    app = _application(user, pk, 'change_dealerapplication')
    _editable(app)
    form = DealerProfileForm(profile)
    if not form.is_valid():
        raise ValidationError([f'{key}: {message}' for key, values in form.errors.items() for message in values])
    app.profile, app.updated_by = _json({name: form.cleaned_data[name] for name in PROFILE_FIELDS}), user
    app.save()
    _review(app, user, 'Profile updated', 'Dealer profile and requested commercial terms updated')
    return app


@transaction.atomic
def upload_document(user, pk, data, files):
    app = _application(user, pk, 'change_dealerapplication')
    _editable(app)
    form = DealerUploadForm(data, files)
    if not form.is_valid():
        raise ValidationError([f'{key}: {message}' for key, values in form.errors.items() for message in values])
    file, content_type, file_name = form.cleaned_data['upload']
    kind = form.cleaned_data['doc_type']
    version = (app.documents.filter(doc_type=kind).aggregate(v=Max('version'))['v'] or 0) + 1
    app.documents.filter(doc_type=kind, is_current=True).update(is_current=False)
    doc = app.documents.create(doc_type=kind, version=version, number=form.cleaned_data.get('number', ''), valid_until=form.cleaned_data.get('valid_until'),
        file=file, content_type=content_type, file_name=Path(file_name).name[:150], size=len(file), digest=hashlib.sha256(file).hexdigest(), uploaded_by=user)
    _review(app, user, 'Document uploaded', f'{doc.get_doc_type_display()} · version {version}')
    return doc


@transaction.atomic
def withdraw_document(user, pk, doc_pk, reason):
    app = _application(user, pk, 'change_dealerapplication')
    _editable(app)
    if not (reason or '').strip():
        raise ValidationError('Record why the evidence is withdrawn.')
    doc = app.documents.select_for_update().get(pk=doc_pk)
    if not doc.is_current:
        raise ValidationError('This version is already historical.')
    doc.is_current = False
    doc.save(update_fields=['is_current'])
    _review(app, user, 'Document withdrawn', f'{doc.get_doc_type_display()} · {reason}')


def _validate_submission(app, policy):
    form = DealerProfileForm(app.profile)
    if not form.is_valid():
        raise ValidationError([f'{key}: {message}' for key, values in form.errors.items() for message in values])
    for field in ['name', 'contact_person', 'phone', 'address', 'district', 'state', 'pan']:
        if not form.cleaned_data.get(field):
            raise ValidationError(f'Complete {form.fields[field].label} before submitting.')
    if policy.get('require_gstin') and not form.cleaned_data.get('gstin'):
        raise ValidationError('Company policy requires a GSTIN.')
    required = set(policy.get('required_documents', []))
    if form.cleaned_data.get('gstin'):
        required.add('gst_certificate')
    if form.cleaned_data.get('bank_account_number'):
        required.add('cancelled_cheque')
    documents = {d.doc_type: d for d in current_documents(app)}
    if required - set(documents):
        labels = dict(PARTNER_DOCUMENT_TYPES)
        raise ValidationError('Required KYC evidence: ' + ', '.join(labels.get(k, k) for k in sorted(required - set(documents))) + '.')
    if any(d.valid_until and d.valid_until < timezone.localdate() for d in documents.values()):
        raise ValidationError('Replace or withdraw expired KYC evidence.')
    gstin = form.cleaned_data.get('gstin')
    if gstin and Partner.objects.filter(kind__in=CUSTOMER_KINDS, gstin__iexact=gstin).exclude(pk=app.partner_id).exists():
        raise ValidationError('This GSTIN already has a customer master. Link that customer and use an amendment instead.')
    if app.partner_id and partner_fingerprint(Partner.objects.get(pk=app.partner_id)) != app.base_partner_fingerprint:
        raise ValidationError('The customer master changed after the amendment was prepared. Withdraw it and prepare a fresh amendment.')


@transaction.atomic
def submit_application(user, pk, note):
    app = _application(user, pk, 'submit_dealerapplication')
    _editable(app)
    if not (note or '').strip():
        raise ValidationError('Record the submission evidence or resubmission note.')
    policy = CRMPolicy.objects.get(pk=1)
    rules = dict(revision=policy.revision, required_documents=policy.required_documents, require_gstin=policy.require_gstin)
    _validate_submission(app, rules)
    app.revision += 1
    app.status, app.submitted_by, app.submitted_at = 'submitted', user, timezone.now()
    app.kyc_by = app.credit_by = app.approved_by = None
    app.approved_credit = app.approved_terms_days = app.approved_at = None
    app.submission_snapshot = _snapshot(app, rules)
    app.save()
    if app.lead.stage != 'won':
        app.lead.stage = 'proposal'
        app.lead.save()
    _review(app, user, 'Submitted', note.strip(), app.submission_snapshot)
    return app


def _independent(app, user):
    makers = set(app.reviews.filter(action__in=MAKER_ACTIONS).values_list('actor_id', flat=True))
    makers.update(app.documents.values_list('uploaded_by_id', flat=True))
    makers.update([app.created_by_id, app.updated_by_id, app.submitted_by_id, app.lead.owner_id])
    for snapshot in app.reviews.filter(action='Owner reassigned').values_list('snapshot', flat=True):
        ownership = snapshot.get('ownership', {})
        makers.update([ownership.get('previous'), ownership.get('recipient')])
    if user.pk in makers:
        raise ValidationError('A contributor to this application cannot approve it (maker-checker).')


@transaction.atomic
def decide_application(user, pk, action, note, credit=None, terms=None):
    lane = {'kyc': 'review_dealerkyc', 'credit': 'review_dealercredit', 'approve': 'approve_dealerapplication'}
    if action in ('changes', 'reject'):
        # Resolve the current lane while holding the common review lock.
        _lock()
        current = DealerApplication.objects.get(pk=pk)
        permission = {'submitted': 'review_dealerkyc', 'kyc': 'review_dealercredit', 'credit': 'approve_dealerapplication'}.get(current.status)
        if not permission:
            raise ValidationError('There is no submitted review to decide.')
    else:
        permission = lane.get(action)
        if not permission:
            raise ValidationError('Unknown dealer review action.')
    app = _application(user, pk, permission)
    _independent(app, user)
    if not (note or '').strip():
        raise ValidationError('Record the review finding and decision evidence.')
    expected = {'kyc': 'submitted', 'credit': 'kyc', 'approve': 'credit'}
    if action in expected and app.status != expected[action]:
        raise ValidationError('Complete the preceding review stage first.')
    if action in ('changes', 'reject'):
        app.status = action if action == 'changes' else 'rejected'
    else:
        _validate_submission(app, app.submission_snapshot.get('policy', {}))
        if _snapshot(app, app.submission_snapshot.get('policy', {})) != app.submission_snapshot:
            raise ValidationError('Profile, owner or evidence changed after submission. Request changes and resubmit.')
        if action == 'kyc':
            app.kyc_by, app.status = user, 'kyc'
        elif action == 'credit':
            if app.kyc_by_id == user.pk:
                raise ValidationError('Finance review must be independent of the KYC reviewer.')
            try:
                amount, days = Decimal(str(credit)), int(terms)
                if not amount.is_finite() or amount < 0 or amount > Decimal(app.profile['credit_limit']) or days < 0 or days > int(app.profile['payment_terms_days']):
                    raise ValueError
                amount = amount.quantize(Decimal('.01'))
            except (ValueError, TypeError, ArithmeticError):
                raise ValidationError('Approved credit and terms must be valid and cannot exceed the requested values.')
            app.approved_credit, app.approved_terms_days, app.credit_by, app.status = amount, days, user, 'credit'
        else:
            if app.credit_by_id == user.pk:
                raise ValidationError('Final approval must be independent of the finance reviewer.')
            partner = Partner.objects.select_for_update().get(pk=app.partner_id) if app.partner_id else Partner(code=next_number('DLR', app.site), active=True)
            for name, value in app.profile.items():
                if name in PROFILE_FIELDS:
                    setattr(partner, name, value)
            partner.credit_limit, partner.payment_terms_days, partner.approved_application = app.approved_credit, app.approved_terms_days, app
            partner.full_clean()
            partner.save()
            partner.documents.all().delete()
            for doc in app.documents.filter(is_current=True):
                PartnerDocument.objects.create(partner=partner, doc_type=doc.doc_type, number=doc.number, valid_until=doc.valid_until, file=bytes(doc.file),
                    file_name=doc.file_name, content_type=doc.content_type, size=doc.size, uploaded_by=doc.uploaded_by)
            app.partner, app.approved_by, app.approved_at, app.status = partner, user, timezone.now(), 'approved'
            app.lead.partner, app.lead.stage, app.lead.next_follow_up = partner, 'won', None
            app.lead.save()
    app.save()
    _review(app, user, dict(kyc='KYC approved', credit='Credit approved', approve='Activated', changes='Changes requested', reject='Rejected')[action], note.strip())
    return app


@transaction.atomic
def withdraw_application(user, pk, reason):
    app = _application(user, pk, 'change_dealerapplication')
    _editable(app)
    if not (reason or '').strip():
        raise ValidationError('Record the reason for withdrawing the application.')
    app.status = 'rejected'
    app.save()
    _review(app, user, 'Withdrawn', reason.strip())


@transaction.atomic
def save_policy(user, required, require_gstin):
    permitted(user, 'manage_crmpolicy')
    policy = _lock()
    if set(required) - set(dict(PARTNER_DOCUMENT_TYPES)):
        raise ValidationError('Choose supported KYC document types.')
    policy.required_documents, policy.require_gstin, policy.updated_by = sorted(set(required)), bool(require_gstin), user
    policy.revision += 1
    policy.save()
    AuditEvent.objects.create(actor=user, action='CRM policy changed', reference='CRM', detail=json.dumps(dict(revision=policy.revision, documents=policy.required_documents, gstin=policy.require_gstin)))
    return policy


@transaction.atomic
def save_target(user, data):
    permitted(user, 'manage_salestarget')
    require_site(user, data['site'])
    _owner(user, data['owner'], data['site'])
    month = data['month'].replace(day=1)
    target, _ = SalesTarget.objects.get_or_create(site=data['site'], owner=data['owner'], month=month, defaults=dict(sales_amount=0, updated_by=user))
    target = SalesTarget.objects.select_for_update().get(pk=target.pk)
    previous = dict(sales_amount=str(target.sales_amount), new_dealers=target.new_dealers, note=target.note)
    for key in ['sales_amount', 'new_dealers', 'note']:
        setattr(target, key, data[key])
    target.updated_by = user
    target.full_clean()
    target.save()
    AuditEvent.objects.create(actor=user, site=target.site, action='CRM target set', reference=f'{month} · {target.owner_id}', detail=json.dumps(dict(previous=previous, current=dict(sales_amount=str(target.sales_amount), new_dealers=target.new_dealers, note=target.note))))
    return target


def performance(user, month):
    permitted(user, 'view_crmperformance')
    import calendar
    start = month.replace(day=1)
    end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
    sites = sites_for(user)
    invoices = Invoice.objects.filter(site__in=sites)
    targets = SalesTarget.objects.filter(site__in=sites, month=start)
    if not user.has_perm('erp.view_team_crm') and not user.is_superuser:
        invoices, targets = invoices.filter(order__created_by=user), targets.filter(owner=user)
    pairs = set(targets.values_list('site_id', 'owner_id')) | set(invoices.values_list('site_id', 'order__created_by_id'))
    approved = DealerApplication.objects.filter(site__in=sites, status='approved', base_partner_fingerprint='', approved_at__date__range=(start, end))
    conversions = {}
    for app in approved:
        owner = app.submission_snapshot.get('owner')
        if user.is_superuser or user.has_perm('erp.view_team_crm') or owner == user.pk:
            pair = (app.site_id, owner)
            pairs.add(pair)
            conversions[pair] = conversions.get(pair, 0) + 1
    rows = []
    for site_id, owner_id in sorted(pairs):
        source = invoices.filter(site_id=site_id, order__created_by_id=owner_id)
        period = source.filter(date__range=(start, end))
        sales = period.aggregate(t=Sum('taxable'))['t'] or ZERO
        credits = CreditNote.objects.filter(invoice__in=source, date__range=(start, end)).aggregate(t=Sum('taxable'))['t'] or ZERO
        collections = Settlement.objects.filter(invoice__in=source, receipt__date__range=(start, end)).filter(receipt__status='posted').aggregate(t=Sum('amount'))['t'] or ZERO
        overdue = sum((inv.outstanding for inv in source.filter(due_date__lt=timezone.localdate()).prefetch_related('settlements')), ZERO)
        target = targets.filter(site_id=site_id, owner_id=owner_id).first()
        actual = sales - credits
        rows.append(dict(site=Site.objects.get(pk=site_id), owner=get_user_model().objects.get(pk=owner_id), target=target,
                         sales=actual, collections=collections, overdue=overdue, new_dealers=conversions.get((site_id, owner_id), 0),
                         achievement=(actual / target.sales_amount * 100).quantize(Decimal('.1')) if target and target.sales_amount else None))
    return rows
