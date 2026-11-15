"""Freight screens: lane rate card, transporter bill entry against trips, bill check (maker-checker)."""
from .gst import inr
from datetime import date
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum, Count
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import freight as F
from .influencer_views import district_options
from .models import FreightRate, FreightBill, Partner, Site, Dispatch, AuditEvent, clean_place
from .services import permitted, scope


class FreightRateForm(forms.ModelForm):
    class Meta:
        model = FreightRate
        fields = ['origin', 'destination', 'transporter', 'vehicle_type', 'basis', 'rate', 'min_mt', 'detention_per_day', 'unloading_charge', 'distance_km',
                  'valid_from', 'valid_to', 'active']
        widgets = {'valid_from': forms.DateInput(attrs={'type': 'date'}), 'valid_to': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['origin'].queryset = Site.objects.filter(active=True)
        self.fields['transporter'].queryset = Partner.objects.filter(kind='Transporter', active=True)
        self.fields['transporter'].empty_label = 'Any transporter'
        self.fields['vehicle_type'].choices = [('', 'Any vehicle')] + list(self.fields['vehicle_type'].choices)[1:]
        self.fields['destination'].widget.attrs.update(list='district-options', autocomplete='off', placeholder='e.g. Bhopal')
        for f in self.fields.values():
            w = f.widget
            w.attrs.setdefault('class', 'form-check-input' if isinstance(w, forms.CheckboxInput) else 'form-select' if isinstance(w, forms.Select) else 'form-control')

    def clean(self):
        data = super().clean()
        data['destination'] = clean_place(data.get('destination'))
        if data.get('valid_to') and data.get('valid_from') and data['valid_to'] < data['valid_from']:
            self.add_error('valid_to', 'End date cannot be before the start date.')
        if data.get('origin') and data.get('destination') and data.get('active') and not self.errors:
            clash = FreightRate.objects.filter(origin=data['origin'], destination__iexact=data['destination'], transporter=data.get('transporter'),
                                               vehicle_type=data.get('vehicle_type') or '', active=True).exclude(pk=self.instance.pk) \
                .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=data['valid_from']))
            if data.get('valid_to'):
                clash = clash.filter(valid_from__lte=data['valid_to'])
            if clash.exists():
                raise forms.ValidationError('A rate for this lane, transporter and vehicle type already covers these dates. End it first, then add the new rate.')
        return data


@login_required
def freight_rates(request):
    permitted(request.user, 'view_freightrate')
    qs = scope(FreightRate.objects.select_related('origin', 'transporter').annotate(trips=Count('dispatches')), request.user, 'origin')
    q = request.GET.get('q', '').strip()
    if q: qs = qs.filter(Q(destination__icontains=q) | Q(transporter__name__icontains=q) | Q(origin__code__icontains=q))
    today = timezone.localdate()
    if request.GET.get('state', 'current') == 'current':
        qs = qs.filter(active=True).filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
    unpriced = sorted({F.destination_of(d) or '—' for d in Dispatch.objects.filter(status__in=['gate_out', 'delivered'], freight_payable_by='company', freight_amount__isnull=True)
                       .select_related('order__partner')})
    return render(request, 'erp/freight_rates.html', {'title': 'Freight rates', 'rates': qs[:300], 'q': q, 'state': request.GET.get('state', 'current'), 'unpriced': unpriced,
                                                      'can_edit': request.user.has_perm('erp.change_freightrate'), 'can_add': request.user.has_perm('erp.add_freightrate'), 'today': today})


@login_required
def freight_rate_form(request, pk=None):
    permitted(request.user, 'change_freightrate' if pk else 'add_freightrate')
    obj = get_object_or_404(scope(FreightRate.objects.all(), request.user, 'origin'), pk=pk) if pk else None
    if obj and obj.dispatches.exists() and request.method == 'POST':
        # Used on trips: only the end date / active flag may change, so history stays true.
        form = FreightRateForm(request.POST, instance=obj)
        for name in form.fields:
            if name not in ('valid_to', 'active'):
                form.fields[name].disabled = True
    else:
        form = FreightRateForm(request.POST or None, instance=obj)
        if obj and obj.dispatches.exists():
            for name in form.fields:
                if name not in ('valid_to', 'active'):
                    form.fields[name].disabled = True
    form.fields['origin'].queryset = (Site.objects.filter(active=True) if request.user.is_superuser else Site.objects.filter(active=True, siteaccess__user=request.user))
    if request.method == 'POST' and form.is_valid():
        r = form.save(commit=False)
        if not pk: r.created_by = request.user
        r.save()
        AuditEvent.objects.create(actor=request.user, action='Freight rate updated' if pk else 'Freight rate added', reference=f'freightrate:{r.pk}',
                                  detail=f'{r.origin.code} → {r.destination} · ₹{r.rate} {r.get_basis_display()}')
        messages.success(request, f'Freight {r.origin.code} → {r.destination} saved.')
        return redirect('freight_rates')
    return render(request, 'erp/logistics_form.html', {'title': 'Edit freight rate' if pk else 'Add freight rate', 'form': form, 'back': 'freight_rates', 'kind': 'rate',
                                                       'note': 'Rates already used on trips can only be ended — add a new rate for the new price.' if obj and obj.dispatches.exists() else
                                                       'Destination is the buyer district. Leave transporter / vehicle empty for the general lane rate.',
                                                       'districts': district_options()})


@login_required
def freight_bills(request):
    permitted(request.user, 'view_freightbill')
    base = F.visible_bills(FreightBill.objects.select_related('transporter', 'created_by', 'checked_by'), request.user)
    qs = base
    status = request.GET.get('status', '')
    if status in ('submitted', 'approved', 'rejected'): qs = qs.filter(status=status)
    pending = Dispatch.objects.filter(status__in=['gate_out', 'delivered'], freight_payable_by='company', freight_line__isnull=True, transporter__isnull=False)
    pending = scope(pending, request.user)
    by_transporter = pending.values('transporter', 'transporter__name').annotate(n=Count('id'), amount=Sum('freight_amount')).order_by('transporter__name')
    return render(request, 'erp/freight_bills.html', {'title': 'Freight bills', 'bills': qs[:200], 'status': status, 'unbilled': by_transporter,
                                                      'totals': base.filter(status='approved').aggregate(p=Sum('payable')),
                                                      'waiting': base.filter(status='submitted').count(),
                                                      'can_add': request.user.has_perm('erp.add_freightbill')})


@login_required
def freight_bill_new(request, transporter_pk):
    permitted(request.user, 'add_freightbill')
    transporter = get_object_or_404(Partner, pk=transporter_pk, kind='Transporter')
    trips = list(scope(F.billable_trips(transporter), request.user))
    for t in trips:
        t.recovery = F.shortage_value(t)
        t.f = {k: request.POST.get(f'{k}_{t.pk}', '') for k in ['pick', 'claimed', 'days', 'extra', 'reason']}
    errors = []
    if request.method == 'POST':
        rows = {}
        for t in trips:
            if request.POST.get(f'pick_{t.pk}') != 'on':
                continue
            try:
                rows[t] = {'claimed': Decimal(t.f['claimed'] or '0'), 'days': int(t.f['days'] or 0), 'extra': Decimal(t.f['extra'] or '0'), 'reason': t.f['reason']}
            except (InvalidOperation, ValueError):
                errors.append(f'{t.number}: enter numbers only.')
        try:
            bill_date = date.fromisoformat(request.POST.get('bill_date', ''))
        except ValueError:
            bill_date = None
            errors.append('Enter the bill date.')
        if not errors:
            try:
                bill = F.create_bill(request.user, transporter, request.POST.get('bill_number', ''), bill_date, rows, request.POST.get('advance') or 0, request.POST.get('notes', ''))
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'Bill {bill.bill_number} entered — payable ₹{inr(bill.payable, 2)} after check. A different user must approve it.')
                return redirect('freight_bill_detail', pk=bill.pk)
    return render(request, 'erp/freight_bill_new.html', {'title': f'Freight bill · {transporter.name}', 'transporter': transporter, 'trips': trips, 'errors': errors,
                                                         'post': request.POST, 'today': timezone.localdate()})


@login_required
def freight_bill_detail(request, pk):
    permitted(request.user, 'view_freightbill')
    bill = get_object_or_404(F.visible_bills(FreightBill.objects.select_related('transporter', 'created_by', 'checked_by'), request.user), pk=pk)
    _,issues=F.review_state(bill) if bill.status=='submitted' else ({},[])
    return render(request, 'erp/freight_bill_detail.html', {'title': f'Freight bill {bill.bill_number}', 'bill': bill,
                                                            'lines': bill.lines.select_related('dispatch__vehicle', 'dispatch__order__partner', 'dispatch__freight_rate'),
                                                            'review_issues':issues,'review_token':F.review_token(bill) if bill.status=='submitted' else '',
                                                            'can_decide': F.can_decide(request.user,bill)})


@login_required
@require_POST
def freight_bill_decide(request, pk, decision):
    if decision not in ('approve', 'reject'):
        from django.http import Http404
        raise Http404
    get_object_or_404(F.visible_bills(FreightBill.objects.all(), request.user), pk=pk)
    try:
        bill = F.decide(request.user, pk, decision == 'approve', request.POST.get('note', ''),expected_evidence=request.POST.get('evidence',''))
        messages.success(request, f'Bill {bill.bill_number} {bill.get_status_display().lower()}.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('freight_bill_detail', pk=pk)
