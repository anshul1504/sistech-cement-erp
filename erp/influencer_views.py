"""Masons, contractors & other influencers: list, district-wise, dealer-wise, detail with activities, CRUD, CSV import/export."""
import csv
import io
from collections import defaultdict
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, Sum, Max
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import InfluencerForm, InfluencerActivityForm, InfluencerImportForm, normalise_mobile
from .models import (Influencer, InfluencerActivity, Partner, AuditEvent, INFLUENCER_KINDS, INFLUENCER_TIERS, INFLUENCER_SPECIALISATIONS,
                     BRAND_PREFERENCES, INDIAN_STATES, CUSTOMER_KINDS, LEAD_KINDS, clean_place)
from .services import permitted

KIND_GROUPS = [('', 'All'), ('Mason', 'Masons'), ('Contractor', 'Contractors'), ('other', 'Engineers, architects & builders')]
OTHER_KINDS = ['Engineer', 'Architect', 'Builder']
TIER_FLOORS = dict(INFLUENCER_TIERS)
TIER_ORDER = [name for name, _ in INFLUENCER_TIERS]


def log(user, action, influencer, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=f'influencer:{influencer.pk}', detail=detail or influencer.code)


def linked_to(dealer_pk):
    return Q(primary_dealer_id=dealer_pk) | Q(dealers__pk=dealer_pk)


def tier_q(tier):
    i = TIER_ORDER.index(tier)
    q = Q(monthly_cement_bags__gte=TIER_FLOORS[tier])
    if i:
        q &= Q(monthly_cement_bags__lt=TIER_FLOORS[TIER_ORDER[i - 1]])
    return q


def filtered(request):
    """The list's queryset and the filter values, shared by the page and the CSV export."""
    f = {k: request.GET.get(k, '').strip() for k in ['group', 'q', 'district', 'dealer', 'tier', 'brand', 'kyc']}
    f['status'] = request.GET.get('status', 'active')
    qs = Influencer.objects.select_related('primary_dealer')
    if f['group'] == 'other': qs = qs.filter(kind__in=OTHER_KINDS)
    elif f['group']: qs = qs.filter(kind=f['group'])
    if f['status'] == 'active': qs = qs.filter(active=True)
    elif f['status'] == 'inactive': qs = qs.filter(active=False)
    if f['district']: qs = qs.filter(district__iexact=f['district'])
    if f['dealer'] == 'none': qs = qs.filter(primary_dealer__isnull=True, dealers__isnull=True)
    elif f['dealer'].isdigit(): qs = qs.filter(linked_to(int(f['dealer'])))
    if f['tier'] in TIER_FLOORS: qs = qs.filter(tier_q(f['tier']))
    if f['brand'] and f['brand'] in dict(BRAND_PREFERENCES): qs = qs.filter(brand_preference=f['brand'])
    if f['kyc'] == 'pending': qs = qs.filter(kyc_verified=False)
    elif f['kyc'] == 'verified': qs = qs.filter(kyc_verified=True)
    if f['q']:
        digits = ''.join(ch for ch in f['q'] if ch.isdigit())
        text = Q(name__icontains=f['q']) | Q(code__icontains=f['q']) | Q(firm_name__icontains=f['q']) | Q(village__icontains=f['q']) | Q(tehsil__icontains=f['q'])
        qs = qs.filter(text | Q(mobile__contains=digits[-10:]) if len(digits) >= 4 else text)
    return qs.distinct(), f


def district_options():
    names = set(Influencer.objects.values_list('district', flat=True)) | {clean_place(d) for d in Partner.objects.exclude(district='').values_list('district', flat=True)}
    return sorted(n for n in names if n)


@login_required
def influencers(request):
    permitted(request.user, 'view_influencer')
    qs, f = filtered(request)
    if request.GET.get('export') == 'csv':
        return export_csv(request, qs)
    active = Influencer.objects.filter(active=True)
    stats = active.aggregate(total=Count('id'), bags=Sum('monthly_cement_bags'), tmt=Sum('monthly_tmt_mt'),
                             masons=Count('id', filter=Q(kind='Mason')), contractors=Count('id', filter=Q(kind='Contractor')),
                             sistech=Count('id', filter=Q(brand_preference__in=['sistech', 'mixed'])), kyc=Count('id', filter=Q(kyc_verified=True)),
                             districts=Count('district', distinct=True))
    stats['unlinked'] = active.filter(primary_dealer__isnull=True, dealers__isnull=True).count()
    tabs = [(code, label, active.filter(kind__in=OTHER_KINDS).count() if code == 'other' else active.filter(kind=code).count() if code else stats['total'])
            for code, label in KIND_GROUPS]
    page = Paginator(qs.order_by('name'), 50).get_page(request.GET.get('page'))
    dealer = Partner.objects.filter(pk=f['dealer']).first() if f['dealer'].isdigit() else None
    params = request.GET.copy(); params.pop('page', None)
    return render(request, 'erp/influencers.html', {
        'title': 'Masons & contractors', 'page': page, 'stats': stats, 'tabs': tabs, 'filters': f, 'dealer': dealer,
        'districts': district_options(), 'tiers': TIER_ORDER, 'brands': BRAND_PREFERENCES[1:], 'querystring': params.urlencode(),
        'dealers': Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).order_by('name'),
        'can_add': request.user.has_perm('erp.add_influencer'), 'can_export': request.user.has_perm('erp.export_influencer'),
        'section': 'list'})


def export_csv(request, qs):
    permitted(request.user, 'export_influencer')
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="masons-contractors-{timezone.localdate():%Y%m%d}.csv"'
    response.write('﻿')  # Excel opens UTF-8 (Hindi names) correctly with a BOM
    writer = csv.writer(response)
    writer.writerow(['Code', 'Type', 'Name', 'Firm', 'Mobile', 'State', 'District', 'Tehsil', 'Village', 'PIN', 'Main dealer', 'Other dealers',
                     'Specialisation', 'Experience (yrs)', 'Cement bags / month', 'TMT MT / month', 'Tier', 'Current brand', 'KYC verified', 'Active'])
    rows = 0
    for i in qs.prefetch_related('dealers').order_by('district', 'name'):
        writer.writerow([i.code, i.get_kind_display(), i.name, i.firm_name, i.mobile, i.get_state_display(), i.district, i.tehsil, i.village, i.pincode,
                         i.primary_dealer.code if i.primary_dealer else '', ' / '.join(d.code for d in i.dealers.all()),
                         i.get_specialisation_display() if i.specialisation else '', i.experience_years or '', i.monthly_cement_bags, i.monthly_tmt_mt,
                         i.tier, i.get_brand_preference_display() if i.brand_preference else '', 'Yes' if i.kyc_verified else 'No', 'Yes' if i.active else 'No'])
        rows += 1
    AuditEvent.objects.create(actor=request.user, action='Masons & contractors exported', reference='CSV', detail=f'{rows} rows')
    return response


@login_required
def influencer_districts(request):
    """District-wise: who is registered where, which dealers serve them, and where coverage is missing."""
    permitted(request.user, 'view_influencer')
    state = request.GET.get('state', '')
    people = Influencer.objects.filter(active=True)
    if state: people = people.filter(state=state)
    rows = {}
    for r in people.values('district').annotate(
            total=Count('id'), masons=Count('id', filter=Q(kind='Mason')), contractors=Count('id', filter=Q(kind='Contractor')),
            others=Count('id', filter=Q(kind__in=OTHER_KINDS)), bags=Sum('monthly_cement_bags'), tmt=Sum('monthly_tmt_mt'),
            sistech=Count('id', filter=Q(brand_preference__in=['sistech', 'mixed'])), kyc=Count('id', filter=Q(kyc_verified=True)),
            unlinked=Count('id', filter=Q(primary_dealer__isnull=True, dealers__isnull=True))):
        rows[r['district']] = {**r, 'dealers': set(), 'linked_dealers': set()}
    # Dealers located in each district (from the dealer master) and dealers the people there actually buy through.
    dealer_qs = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).exclude(district='')
    if state: dealer_qs = dealer_qs.filter(state=state)
    for pk, district in dealer_qs.values_list('pk', 'district'):
        rows.setdefault(clean_place(district), {'district': clean_place(district), 'total': 0, 'masons': 0, 'contractors': 0, 'others': 0, 'bags': 0, 'tmt': 0,
                                                 'sistech': 0, 'kyc': 0, 'unlinked': 0, 'dealers': set(), 'linked_dealers': set()})['dealers'].add(pk)
    for district, primary in people.exclude(primary_dealer=None).values_list('district', 'primary_dealer'):
        rows[district]['linked_dealers'].add(primary)
    for district, dealer in people.exclude(dealers=None).values_list('district', 'dealers'):
        rows[district]['linked_dealers'].add(dealer)
    leads = dict(InfluencerActivity.objects.filter(influencer__in=people, kind__in=LEAD_KINDS, status='open')
                 .values_list('influencer__district').annotate(n=Count('id')).values_list('influencer__district', 'n'))
    table = []
    for row in rows.values():
        row['dealer_count'] = len(row['dealers'])
        row['linked_count'] = len(row['linked_dealers'])
        row['open_leads'] = leads.get(row['district'], 0)
        row['share'] = round(row['sistech'] * 100 / row['total']) if row['total'] else 0
        row['gap'] = ('no_people' if not row['total'] else 'no_dealer' if not row['dealer_count'] and not row['linked_count']
                      else 'unlinked' if row['unlinked'] else 'low_share' if row['share'] < 40 else '')
        table.append(row)
    sort = request.GET.get('sort', 'total')
    key = {'district': lambda r: r['district'].lower(), 'bags': lambda r: -(r['bags'] or 0), 'share': lambda r: r['share'], 'dealers': lambda r: -r['dealer_count']}.get(sort, lambda r: -r['total'])
    table.sort(key=lambda r: (key(r), r['district'].lower()))
    totals = {k: sum((r[k] or 0) for r in table) for k in ['total', 'masons', 'contractors', 'others', 'bags', 'tmt', 'open_leads', 'unlinked']}
    gaps = {g: sum(1 for r in table if r['gap'] == g) for g in ['no_people', 'no_dealer', 'unlinked', 'low_share']}
    states = [s for s in INDIAN_STATES if s[0] in set(Influencer.objects.values_list('state', flat=True))]
    return render(request, 'erp/influencer_districts.html', {
        'title': 'Masons & contractors · District-wise', 'rows': table, 'totals': totals, 'gaps': gaps, 'sort': sort, 'state': state, 'states': states,
        'max_total': max([r['total'] for r in table] or [1]) or 1, 'can_add': request.user.has_perm('erp.add_influencer'), 'section': 'districts'})


@login_required
def influencer_dealers(request):
    """Dealer-wise: the masons and contractors behind each dealer, and dealers with nobody linked."""
    permitted(request.user, 'view_influencer')
    district, q = request.GET.get('district', '').strip(), request.GET.get('q', '').strip()
    dealers = Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True)
    if district: dealers = dealers.filter(district__iexact=district)
    if q: dealers = dealers.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(city__icontains=q) | Q(territory__icontains=q))
    stats = defaultdict(lambda: {'total': 0, 'masons': 0, 'contractors': 0, 'others': 0, 'bags': 0, 'tmt': Decimal('0'), 'primary': 0, 'districts': set(), 'sistech': 0})
    people = Influencer.objects.filter(active=True).filter(Q(primary_dealer__in=dealers) | Q(dealers__in=dealers)).distinct().prefetch_related('dealers')
    for person in people:
        for d in person.all_dealers():
            s = stats[d.pk]
            s['total'] += 1
            s['masons' if person.kind == 'Mason' else 'contractors' if person.kind == 'Contractor' else 'others'] += 1
            s['bags'] += person.monthly_cement_bags
            s['tmt'] += person.monthly_tmt_mt
            s['primary'] += person.primary_dealer_id == d.pk
            s['sistech'] += person.brand_preference in ('sistech', 'mixed')
            s['districts'].add(person.district)
    last = dict(InfluencerActivity.objects.filter(dealer__in=dealers).values('dealer').annotate(d=Max('date')).values_list('dealer', 'd'))
    table = []
    for d in dealers.order_by('name'):
        s = stats.get(d.pk) or stats.default_factory()
        table.append({'dealer': d, **s, 'districts': sorted(s['districts']), 'last_activity': last.get(d.pk),
                      'share': round(s['sistech'] * 100 / s['total']) if s['total'] else 0})
    sort = request.GET.get('sort', 'total')
    if sort == 'name': table.sort(key=lambda r: r['dealer'].name.lower())
    elif sort == 'bags': table.sort(key=lambda r: -r['bags'])
    elif sort == 'empty': table.sort(key=lambda r: (r['total'], r['dealer'].name.lower()))
    else: table.sort(key=lambda r: (-r['total'], r['dealer'].name.lower()))
    unlinked = Influencer.objects.filter(active=True, primary_dealer__isnull=True, dealers__isnull=True).count()
    return render(request, 'erp/influencer_dealers.html', {
        'title': 'Masons & contractors · Dealer-wise', 'rows': table, 'district': district, 'q': q, 'sort': sort, 'districts': district_options(),
        'unlinked': unlinked, 'empty_dealers': sum(1 for r in table if not r['total']), 'max_total': max([r['total'] for r in table] or [1]) or 1,
        'section': 'dealers'})


@login_required
def influencer_detail(request, pk):
    permitted(request.user, 'view_influencer')
    person = get_object_or_404(Influencer.objects.select_related('primary_dealer', 'created_by'), pk=pk)
    activities = person.activities.select_related('dealer', 'created_by')
    leads = [a for a in activities if a.is_lead]
    can_change = request.user.has_perm('erp.change_influencer')
    nearby = Influencer.objects.filter(active=True, district=person.district).exclude(pk=person.pk)
    return render(request, 'erp/influencer_detail.html', {
        'title': person.name, 'person': person, 'dealers': person.all_dealers(), 'activities': activities[:30], 'activity_count': activities.count(),
        'leads': {'open': sum(a.status == 'open' for a in leads), 'won': sum(a.status == 'won' for a in leads), 'lost': sum(a.status == 'lost' for a in leads),
                  'bags': sum(a.expected_bags for a in leads if a.status == 'open')},
        'last_activity': activities.first(), 'nearby': nearby.count(), 'can_change': can_change,
        'form': InfluencerActivityForm(influencer=person) if can_change else None,
        'tier_next': next(((name, floor - person.monthly_cement_bags) for name, floor in reversed(INFLUENCER_TIERS) if floor > person.monthly_cement_bags), None),
        'history': AuditEvent.objects.filter(reference=f'influencer:{person.pk}').select_related('actor').order_by('-created_at')[:6]})


@login_required
def influencer_form(request, pk=None):
    permitted(request.user, 'change_influencer' if pk else 'add_influencer')
    person = get_object_or_404(Influencer, pk=pk) if pk else None
    initial = {}
    if not pk:
        dealer = Partner.objects.filter(pk=request.GET.get('dealer'), kind__in=CUSTOMER_KINDS).first() if request.GET.get('dealer', '').isdigit() else None
        if dealer:
            initial.update(primary_dealer=dealer.pk, district=clean_place(dealer.district), state=dealer.state or '23')
        if request.GET.get('district'): initial['district'] = request.GET['district']
        if request.GET.get('kind') in dict(INFLUENCER_KINDS): initial['kind'] = request.GET['kind']
    form = InfluencerForm(request.POST or None, instance=person, initial=initial)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            saved = form.save(commit=False)
            if not pk: saved.created_by = request.user
            saved.save()
            form.save_m2m()
            if saved.primary_dealer_id:
                saved.dealers.remove(saved.primary_dealer_id)  # main dealer is listed once
            log(request.user, 'Influencer updated' if pk else 'Influencer created', saved, f'{saved.code} · {saved.get_kind_display()} · {saved.district}')
        messages.success(request, f'{saved.name} {"updated" if pk else "added"}.')
        if not pk and request.POST.get('another'):
            return redirect(f"{request.path}?district={saved.district}&dealer={saved.primary_dealer_id or ''}&kind={saved.kind}")
        return redirect('influencer_detail', pk=saved.pk)
    return render(request, 'erp/influencer_form.html', {'title': 'Edit mason / contractor' if pk else 'Add mason / contractor', 'form': form,
                                                        'person': person, 'districts': district_options(), 'section': 'list'})


@login_required
@require_POST
def influencer_status(request, pk):
    permitted(request.user, 'change_influencer')
    person = get_object_or_404(Influencer, pk=pk)
    person.active = not person.active
    person.save(update_fields=['active', 'updated_at'])
    log(request.user, 'Influencer activated' if person.active else 'Influencer deactivated', person)
    messages.success(request, f'{person.name} is {"active again" if person.active else "now inactive — hidden from counts and lists"}.')
    return redirect('influencer_detail', pk=person.pk)


@login_required
@require_POST
def influencer_delete(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied
    person = get_object_or_404(Influencer, pk=pk)
    if person.activities.exists():
        messages.error(request, f'{person.name} has activity history, so it cannot be deleted. Deactivate instead.')
        return redirect('influencer_detail', pk=person.pk)
    log(request.user, 'Influencer deleted', person, f'{person.code} · {person.name}')
    name = person.name
    person.delete()
    messages.success(request, f'{name} deleted.')
    return redirect('influencers')


# ---------- Activities ----------

@login_required
@require_POST
def activity_new(request, pk):
    permitted(request.user, 'change_influencer')
    person = get_object_or_404(Influencer, pk=pk)
    form = InfluencerActivityForm(request.POST, influencer=person)
    if form.is_valid():
        activity = form.save(commit=False)
        activity.influencer, activity.created_by = person, request.user
        activity.save()
        log(request.user, 'Influencer activity logged', person, f'{activity.get_kind_display()} · {activity.date:%d %b %Y}')
        messages.success(request, f'{activity.get_kind_display()} logged for {person.name}.')
        return redirect('influencer_detail', pk=person.pk)
    messages.error(request, 'Activity not saved — ' + '; '.join(f'{form.fields[f].label if f in form.fields else ""}: {e[0]}'.lstrip(': ') for f, e in form.errors.items()))
    return redirect('influencer_detail', pk=person.pk)


@login_required
@require_POST
def activity_status(request, pk, activity_pk):
    permitted(request.user, 'change_influencer')
    activity = get_object_or_404(InfluencerActivity, pk=activity_pk, influencer_id=pk, kind__in=LEAD_KINDS)
    status = request.POST.get('status')
    if status in ('open', 'won', 'lost'):
        activity.status = status
        activity.save(update_fields=['status'])
        log(request.user, 'Influencer lead updated', activity.influencer, f'{activity.title} → {activity.get_status_display()}')
        messages.success(request, f'"{activity.title}" marked {activity.get_status_display().lower()}.')
    return redirect('influencer_detail', pk=pk)


@login_required
@require_POST
def activity_delete(request, pk, activity_pk):
    permitted(request.user, 'change_influencer')
    activity = get_object_or_404(InfluencerActivity, pk=activity_pk, influencer_id=pk)
    if not request.user.is_superuser and activity.created_by_id != request.user.pk:
        raise PermissionDenied
    if hasattr(activity, 'sales_project'):
        messages.error(request, 'This referral is retained by a project. Keep the source history.')
        return redirect('influencer_detail', pk=pk)
    log(request.user, 'Influencer activity removed', activity.influencer, f'{activity.get_kind_display()} · {activity.date:%d %b %Y}')
    activity.delete()
    messages.success(request, 'Activity removed.')
    return redirect('influencer_detail', pk=pk)


# ---------- CSV import ----------

IMPORT_COLUMNS = ['type', 'name', 'mobile', 'district', 'tehsil', 'village', 'state', 'pincode', 'firm_name', 'dealer_code',
                  'monthly_cement_bags', 'monthly_tmt_mt', 'specialisation', 'experience_years', 'brand', 'notes']
KIND_ALIASES = {'mason': 'Mason', 'mistri': 'Mason', 'mistry': 'Mason', 'mason / mistri': 'Mason', 'contractor': 'Contractor', 'thekedar': 'Contractor',
                'engineer': 'Engineer', 'site engineer': 'Engineer', 'architect': 'Architect', 'builder': 'Builder', 'developer': 'Builder', 'builder / developer': 'Builder'}
MAX_IMPORT_ROWS = 2000
STATE_ABBR = {'JK': '01', 'HP': '02', 'PB': '03', 'CH': '04', 'UK': '05', 'UT': '05', 'HR': '06', 'DL': '07', 'RJ': '08', 'UP': '09', 'BR': '10',
              'AS': '18', 'WB': '19', 'JH': '20', 'OD': '21', 'OR': '21', 'CG': '22', 'CT': '22', 'MP': '23', 'GJ': '24', 'MH': '27', 'KA': '29',
              'GA': '30', 'KL': '32', 'TN': '33', 'TS': '36', 'TG': '36', 'AP': '37'}


def _choice(value, choices):
    v = (value or '').strip().lower()
    for code, label in choices:
        if v and v in (code.lower(), label.lower()):
            return code
    return ''


def import_row(raw, dealers):
    """CSV row → InfluencerForm data. Returns (data, problems that stop the row)."""
    row = {k.strip().lower().replace(' ', '_'): (v or '').strip() for k, v in raw.items() if k}
    problems = []
    kind = KIND_ALIASES.get(row.get('type', '').lower(), '')
    if not kind: problems.append(f'unknown type "{row.get("type", "")}"')
    state = row.get('state', '')
    state_code = (state if state in dict(INDIAN_STATES) else STATE_ABBR.get(state.upper()) or _choice(state, INDIAN_STATES)) if state else '23'
    if not state_code: problems.append(f'unknown state "{state}"')
    dealer = None
    if row.get('dealer_code'):
        dealer = dealers.get(row['dealer_code'].upper())
        if not dealer: problems.append(f'dealer code {row["dealer_code"]} not found')
    data = {'kind': kind, 'name': row.get('name', ''), 'firm_name': row.get('firm_name', ''), 'mobile': row.get('mobile', ''), 'alt_mobile': '',
            'whatsapp': 'on', 'active': 'on', 'state': state_code, 'district': row.get('district', ''), 'tehsil': row.get('tehsil', ''),
            'village': row.get('village', ''), 'pincode': row.get('pincode', ''), 'address': '', 'primary_dealer': dealer.pk if dealer else '',
            'specialisation': _choice(row.get('specialisation'), INFLUENCER_SPECIALISATIONS[1:]), 'experience_years': row.get('experience_years', ''),
            'team_size': '', 'monthly_cement_bags': row.get('monthly_cement_bags') or '0', 'monthly_tmt_mt': row.get('monthly_tmt_mt') or '0',
            'brand_preference': _choice(row.get('brand'), BRAND_PREFERENCES[1:]) or {'sistech': 'sistech', 'other': 'competitor', 'both': 'mixed'}.get(row.get('brand', '').lower(), ''),
            'aadhaar_last4': '', 'pan': '', 'upi_id': '', 'date_of_birth': '', 'notes': row.get('notes', '')}
    return data, problems


@login_required
def influencer_import(request):
    permitted(request.user, 'add_influencer')
    if request.GET.get('template'):
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="masons-contractors-template.csv"'
        response.write('﻿')
        writer = csv.writer(response)
        writer.writerow(IMPORT_COLUMNS)
        writer.writerow(['Mason', 'Ramesh Kushwaha', '9876543210', 'Bhopal', 'Huzur', 'Kolar', 'MP', '462042', '', 'DLR-BPL-001', '300', '0.5', 'Residential houses', '12', 'Sistech', ''])
        writer.writerow(['Contractor', 'Suresh Patel', '9123456780', 'Sehore', 'Ashta', 'Ashta', 'Madhya Pradesh', '', 'Patel Constructions', '', '900', '4', 'RCC / slab casting', '8', 'Other brand only', 'Builds 3-4 houses a year'])
        return response
    form = InfluencerImportForm(request.POST or None, request.FILES or None)
    result = None
    if request.method == 'POST' and form.is_valid():
        try:
            text = form.cleaned_data['file'].read().decode('utf-8-sig')
        except UnicodeDecodeError:
            form.add_error('file', 'The file is not UTF-8. In Excel use File → Save As → "CSV UTF-8 (Comma delimited)".')
            text = None
        if text is not None:
            reader = csv.DictReader(io.StringIO(text))
            headers = {(h or '').strip().lower().replace(' ', '_') for h in reader.fieldnames or []}
            missing = {'type', 'name', 'mobile', 'district'} - headers
            if missing:
                form.add_error('file', f'Missing column(s): {", ".join(sorted(missing))}. Download the template to see the layout.')
            else:
                result = run_import(request.user, reader)
                if result['created']:
                    messages.success(request, f'{result["created"]} mason{"s" if result["created"] != 1 else ""} / contractors imported.')
                elif not result['errors'] and not result['duplicates']:
                    messages.info(request, 'The file had no rows to import.')
    return render(request, 'erp/influencer_import.html', {'title': 'Import masons & contractors', 'form': form, 'result': result,
                                                          'columns': IMPORT_COLUMNS, 'section': 'list'})


def run_import(user, reader):
    dealers = {p.code.upper(): p for p in Partner.objects.filter(kind__in=CUSTOMER_KINDS)}
    created, duplicates, errors, seen = 0, [], [], {}
    rows = list(reader)
    if len(rows) > MAX_IMPORT_ROWS:
        return {'created': 0, 'duplicates': [], 'errors': [(0, '', f'File has {len(rows)} rows — the limit is {MAX_IMPORT_ROWS}. Split it into smaller files.')], 'rows': len(rows)}
    with transaction.atomic():
        for n, raw in enumerate(rows, start=2):  # row 1 is the header
            if not any((v or '').strip() for v in raw.values() if isinstance(v, str)):
                continue
            data, problems = import_row(raw, dealers)
            mobile = normalise_mobile(data['mobile'])
            if mobile and mobile in seen:
                duplicates.append((n, data['name'], f'same mobile as row {seen[mobile]} in this file'))
                continue
            existing = Influencer.objects.filter(mobile=mobile).first() if mobile else None
            if existing:
                duplicates.append((n, data['name'], f'already registered as {existing.name} ({existing.code})'))
                continue
            form = InfluencerForm(data)
            if problems or not form.is_valid():
                messages_ = problems + [f'{form.fields[f].label}: {e[0]}' if f in form.fields else e[0] for f, e in (form.errors.items() if not problems else [])]
                errors.append((n, data['name'] or '—', '; '.join(messages_)))
                continue
            person = form.save(commit=False)
            person.created_by = user
            person.save()
            seen[mobile] = n
            created += 1
        if created:
            AuditEvent.objects.create(actor=user, action='Masons & contractors imported', reference='CSV', detail=f'{created} created, {len(duplicates)} duplicates, {len(errors)} errors')
    return {'created': created, 'duplicates': duplicates, 'errors': errors, 'rows': len(rows)}
