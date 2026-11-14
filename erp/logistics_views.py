"""Logistics screens: vehicles & drivers, gate & dispatch board, truck page with one action per step, weighment slip / gate pass, POD."""
import re
from .gst import inr
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Count
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from . import logistics as L
from .forms import normalise_mobile
from .models import (Vehicle, Driver, Dispatch, Order, Partner, Site, AuditEvent, Organization, VEHICLE_TYPES, DISPATCH_STATUSES, INDIAN_STATES)
from .services import permitted, scope


def _style(form):
    for f in form.fields.values():
        w = f.widget
        w.attrs.setdefault('class', 'form-check-input' if isinstance(w, forms.CheckboxInput) else 'form-select' if isinstance(w, forms.Select) else 'form-control')
    return form


# ---------- Vehicles & drivers ----------

class VehicleForm(forms.ModelForm):
    class Meta:
        model = Vehicle
        fields = ['registration', 'vehicle_type', 'capacity_mt', 'owner', 'transporter', 'rc_valid_until', 'insurance_valid_until', 'permit_valid_until',
                  'fitness_valid_until', 'puc_valid_until', 'gps_device', 'active', 'blacklisted', 'blacklist_reason', 'notes']
        widgets = {f: forms.DateInput(attrs={'type': 'date'}) for f in ['rc_valid_until', 'insurance_valid_until', 'permit_valid_until', 'fitness_valid_until', 'puc_valid_until']}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['transporter'].queryset = Partner.objects.filter(kind='Transporter', active=True).order_by('name')
        self.fields['transporter'].empty_label = 'None / not applicable'
        self.fields['registration'] = forms.CharField(label='Vehicle no.', max_length=20, help_text='e.g. MP04GA1234 (spaces are removed).',
                                                      widget=forms.TextInput(attrs={'placeholder': 'MP04GA1234', 'style': 'text-transform:uppercase', 'autocomplete': 'off'}))
        self.fields['capacity_mt'].widget.attrs.update(step='0.01')
        _style(self)

    def clean_registration(self):
        v = re.sub(r'[\s-]', '', self.cleaned_data['registration'].upper())
        if Vehicle.objects.filter(registration=v).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError(f'{v} is already registered.')
        if not re.fullmatch(r'[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4}|\d{2}BH\d{4}[A-Z]{1,2}', v):
            raise forms.ValidationError('Enter a valid registration, e.g. MP04GA1234 or 22BH1234AA.')
        return v

    def clean(self):
        data = super().clean()
        if data.get('owner') == 'transporter' and not data.get('transporter'):
            self.add_error('transporter', 'Choose the transporter who owns this vehicle.')
        if data.get('blacklisted') and not (data.get('blacklist_reason') or '').strip():
            self.add_error('blacklist_reason', 'Record why the vehicle is blacklisted.')
        return data


class DriverForm(forms.ModelForm):
    class Meta:
        model = Driver
        fields = ['name', 'mobile', 'licence_number', 'licence_valid_until', 'transporter', 'active', 'notes']
        widgets = {'licence_valid_until': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['transporter'].queryset = Partner.objects.filter(kind='Transporter', active=True).order_by('name')
        self.fields['transporter'].empty_label = 'Independent / own'
        self.fields['mobile'] = forms.CharField(max_length=16, widget=forms.TextInput(attrs={'inputmode': 'tel', 'placeholder': '98765 43210'}))
        self.fields['licence_number'] = forms.CharField(label='Driving licence no.', max_length=30,
                                                        widget=forms.TextInput(attrs={'placeholder': 'MP04 20190012345', 'style': 'text-transform:uppercase'}))
        _style(self)

    def clean_mobile(self):
        m = normalise_mobile(self.cleaned_data['mobile'])
        if not m: raise forms.ValidationError('Enter a valid 10-digit mobile number.')
        if Driver.objects.filter(mobile=m).exclude(pk=self.instance.pk).exists(): raise forms.ValidationError('Another driver has this mobile.')
        return m

    def clean_licence_number(self):
        v = re.sub(r'[\s-]', '', self.cleaned_data['licence_number'].upper())
        if not re.fullmatch(r'[A-Z]{2}\d{2}\d{4}\d{7}|[A-Z]{2}\d{13}|[A-Z]{2}\d{2}[A-Z0-9]{7,12}', v):
            raise forms.ValidationError('Enter the licence number as printed, e.g. MP0420190012345.')
        if Driver.objects.filter(licence_number=v).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Another driver has this licence number.')
        return v


@login_required
def vehicles(request):
    permitted(request.user, 'view_vehicle')
    tab, q = request.GET.get('tab', 'vehicles'), request.GET.get('q', '').strip()
    today = timezone.localdate()
    vs = Vehicle.objects.select_related('transporter')
    ds = Driver.objects.select_related('transporter')
    if q:
        vs = vs.filter(Q(registration__icontains=q.replace(' ', '')) | Q(transporter__name__icontains=q))
        ds = ds.filter(Q(name__icontains=q) | Q(mobile__icontains=q) | Q(licence_number__icontains=q))
    vlist = list(vs)
    for v in vlist:
        v.issues, v.soon = v.document_issues(today), v.expiring()
    stats = {'vehicles': len(vlist), 'blocked': sum(1 for v in vlist if v.issues or v.blacklisted or not v.active), 'expiring': sum(1 for v in vlist if v.soon and not v.issues),
             'drivers': ds.count(), 'licence_expired': ds.filter(licence_valid_until__lt=today).count()}
    return render(request, 'erp/vehicles.html', {'title': 'Vehicles & drivers', 'tab': tab, 'q': q, 'vehicles': vlist, 'drivers': ds, 'stats': stats, 'today': today,
                                                 'can_edit': request.user.has_perm('erp.change_vehicle'), 'can_add': request.user.has_perm('erp.add_vehicle')})


@login_required
def vehicle_form(request, pk=None):
    permitted(request.user, 'change_vehicle' if pk else 'add_vehicle')
    obj = get_object_or_404(Vehicle, pk=pk) if pk else None
    form = VehicleForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        v = form.save()
        AuditEvent.objects.create(actor=request.user, action='Vehicle updated' if pk else 'Vehicle added', reference=v.registration, detail=v.get_vehicle_type_display())
        messages.success(request, f'{v.registration} saved.' + (' Documents are missing or expired — it cannot enter the gate yet.' if v.document_issues() else ''))
        return redirect(f"{redirect('vehicles').url}?q={v.registration}")
    return render(request, 'erp/logistics_form.html', {'title': 'Edit vehicle' if pk else 'Add vehicle', 'form': form, 'back': 'vehicles', 'kind': 'vehicle',
                                                       'note': 'All five documents must be valid for the truck to pass gate-in.'})


@login_required
def driver_form(request, pk=None):
    permitted(request.user, 'change_vehicle' if pk else 'add_vehicle')
    obj = get_object_or_404(Driver, pk=pk) if pk else None
    form = DriverForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        d = form.save()
        AuditEvent.objects.create(actor=request.user, action='Driver updated' if pk else 'Driver added', reference=d.licence_number, detail=d.name)
        messages.success(request, f'{d.name} saved.')
        return redirect(f"{redirect('vehicles').url}?tab=drivers&q={d.mobile}")
    return render(request, 'erp/logistics_form.html', {'title': 'Edit driver' if pk else 'Add driver', 'form': form, 'back': 'vehicles', 'kind': 'driver',
                                                       'note': 'A driver with an expired licence is stopped at the gate.'})


# ---------- Board ----------

BOARD = [('planned', 'Expected', 'planned'), ('at_gate', 'Inside · tare & queue', 'gate_in tared'), ('loading', 'Loading', 'loading loaded'),
         ('weighed', 'Weighed · invoice & exit', 'weighed'), ('transit', 'In transit', 'gate_out')]


@login_required
def dispatch_board(request):
    permitted(request.user, 'view_dispatch')
    base = scope(Dispatch.objects.select_related('order__partner', 'vehicle', 'driver', 'site'), request.user)
    site = request.GET.get('site', '')
    if site.isdigit(): base = base.filter(site_id=site)
    q = request.GET.get('q', '').strip()
    if q: base = base.filter(Q(number__icontains=q) | Q(vehicle__registration__icontains=q.replace(' ', '')) | Q(order__number__icontains=q) | Q(order__partner__name__icontains=q))
    columns = [(key, label, list(base.filter(status__in=states.split()).order_by('planned_for', 'token'))) for key, label, states in BOARD]
    today = timezone.localdate()
    done = base.filter(Q(status='delivered') | Q(status='cancelled')).order_by('-created_at')[:15]
    stats = {'today': base.filter(gate_out_at__date=today).count(), 'inside': base.filter(status__in=['gate_in', 'tared', 'loading', 'loaded', 'weighed']).count(),
             'exceptions': base.filter(status='weighed').exclude(exception='').filter(exception_approved_by__isnull=True).count(),
             'pod_due': base.filter(status='gate_out').count()}
    ready = scope(Order.objects.filter(kind='Sale', status__in=['Approved', 'Part dispatched'], site__uses_weighbridge=True), request.user).select_related('partner', 'site')[:20]
    return render(request, 'erp/dispatch_board.html', {'title': 'Gate & dispatch', 'columns': columns, 'done': done, 'stats': stats, 'q': q, 'site': site,
                                                       'sites': Site.objects.filter(active=True, uses_weighbridge=True) if request.user.is_superuser else Site.objects.filter(active=True, uses_weighbridge=True, siteaccess__user=request.user),
                                                       'ready': ready if request.user.has_perm('erp.add_dispatch') else []})


# ---------- Plan ----------

@login_required
def dispatch_new(request, order_pk):
    permitted(request.user, 'add_dispatch')
    order = get_object_or_404(scope(Order.objects.select_related('site', 'partner'), request.user), pk=order_pk, kind='Sale')
    lines = [l for l in order.lines.select_related('product') if l.pending_quantity > 0]
    for l in lines:
        l.posted = request.POST.get(f'qty_{l.pk}', '')
    vehicles = list(Vehicle.objects.filter(active=True, blacklisted=False).select_related('transporter'))
    for v in vehicles:
        v.problems = L.vehicle_problems(v)
    drivers = Driver.objects.filter(active=True).order_by('name')
    errors = []
    if request.method == 'POST':
        vehicle = Vehicle.objects.filter(pk=request.POST.get('vehicle') or 0).first()
        driver = Driver.objects.filter(pk=request.POST.get('driver') or 0).first()
        if not vehicle: errors.append('Choose a vehicle.')
        if not driver: errors.append('Choose a driver.')
        qty = {}
        for l in lines:
            raw = request.POST.get(f'qty_{l.pk}', '').strip()
            try:
                qty[l] = Decimal(raw) if raw else Decimal('0')
            except InvalidOperation:
                errors.append(f'{l.product.name}: enter a number.')
        if not errors:
            try:
                d = L.plan(request.user, order, vehicle, driver, qty, bay=request.POST.get('bay', '')[:30], lr_number=request.POST.get('lr_number', '')[:30],
                           transporter=Partner.objects.filter(pk=request.POST.get('transporter') or 0, kind='Transporter').first())
            except ValidationError as e:
                errors += e.messages
            else:
                messages.success(request, f'{d.number} planned — token {d.token} for {d.vehicle}.')
                return redirect('dispatch_detail', pk=d.pk)
    return render(request, 'erp/dispatch_new.html', {'title': f'Plan truck · {order.number}', 'order': order, 'lines': lines, 'vehicles': vehicles, 'drivers': drivers,
                                                     'transporters': Partner.objects.filter(kind='Transporter', active=True), 'errors': errors, 'post': request.POST})


# ---------- Truck page ----------

@login_required
def dispatch_detail(request, pk):
    permitted(request.user, 'view_dispatch')
    d = get_object_or_404(scope(Dispatch.objects.select_related('order__partner', 'vehicle', 'driver', 'site', 'transporter', 'gate_in_by', 'loaded_by', 'exception_approved_by'), request.user), pk=pk)
    lines = list(d.lines.select_related('order_line__product', 'batch'))
    if d.status == 'loading':
        for l in lines:
            l.batch_options = list(L.batches_for(d.site, l.order_line.product)[:20])
    weighments = list(d.weighments.select_related('captured_by', 'superseded_by'))
    flow = ['planned', 'gate_in', 'tared', 'loading', 'loaded', 'weighed', 'gate_out', 'delivered']
    labels = ['Planned', 'Gate-in', 'Tare', 'Loading', 'Loaded', 'Gross & invoice', 'Gate-out', 'POD']
    at = flow.index(d.status) if d.status in flow else -1
    steps = [(labels[i], 'cancelled' if d.status == 'cancelled' else 'done' if i < at else 'current' if i == at else 'todo') for i in range(len(flow))]
    u = request.user
    return render(request, 'erp/dispatch_detail.html', {
        'title': d.number, 'd': d, 'lines': lines, 'weighments': weighments, 'steps': steps, 'checks': L.GATE_CHECKS,
        'vehicle_problems': L.vehicle_problems(d.vehicle, exclude=d.pk) if d.status == 'planned' else [],
        'invoice': getattr(d, 'invoice', None), 'net': d.net_kg, 'loaded_mt': sum(((l.unit_kg() or 0) * l.quantity for l in lines), Decimal('0')) / 1000,
        'can': {'gate': u.has_perm('erp.gate_dispatch'), 'weigh': u.has_perm('erp.weigh_dispatch'), 'load': u.has_perm('erp.load_dispatch'),
                'approve': u.has_perm('erp.approve_weight_exception'), 'invoice': u.has_perm('erp.post_invoice'), 'pod': u.has_perm('erp.pod_dispatch'),
                'cancel': u.has_perm('erp.add_dispatch') or u.has_perm('erp.gate_dispatch')},
        'history': AuditEvent.objects.filter(reference=d.number).select_related('actor').order_by('-created_at')[:12]})


POD_TYPES = {b'%PDF': 'application/pdf', b'\x89PNG': 'image/png', b'\xff\xd8\xff': 'image/jpeg', b'RIFF': 'image/webp'}


@login_required
@require_POST
def dispatch_action(request, pk, action):
    d = get_object_or_404(scope(Dispatch.objects.all(), request.user), pk=pk)
    P, u = request.POST, request.user
    try:
        if action == 'gate_in':
            L.gate_in(u, pk, {k: P.get(k) == 'on' for k, _ in L.GATE_CHECKS}); msg = 'Gate-in recorded. Send the truck to the weighbridge for tare.'
        elif action in ('tare', 'gross'):
            w = L.weigh(u, pk, action, P.get('weight_kg'), P.get('source', 'manual'), P.get('reason', '')); msg = f'{action.title()} {w.weight_kg:,} kg recorded.'
        elif action == 'start_loading':
            L.start_loading(u, pk, P.get('bay', '')); msg = 'Loading started.'
        elif action == 'complete_loading':
            L.complete_loading(u, pk, {k[4:]: v for k, v in P.items() if k.startswith('qty_')}, P.get('seal', ''), P.get('team', ''), P.get('damaged_bags') or 0, P.get('notes', ''),
                               {k[6:]: v for k, v in P.items() if k.startswith('batch_') and v})
            msg = 'Loading complete. Send the truck for gross weight.'
        elif action == 'approve':
            L.approve_exception(u, pk, P.get('note', '')); msg = 'Exception approved.'
        elif action == 'invoice':
            inv = L.invoice(u, pk, P.get('eway_bill', '')); msg = f'Invoice {inv.number} posted for ₹{inr(inv.grand_total, 0)}.'
        elif action == 'gate_out':
            L.gate_out(u, pk); msg = 'Gate-out done. Truck is in transit.'
        elif action == 'deliver':
            f, file = request.FILES.get('pod_file'), None
            if f:
                data = f.read(5 * 1024 * 1024 + 1)
                kind = next((t for sig, t in POD_TYPES.items() if data.startswith(sig)), None)
                if len(data) > 5 * 1024 * 1024 or not kind:
                    raise ValidationError('POD file must be a PDF, PNG, JPEG or WebP up to 5 MB.')
                file = (data, kind)
            L.deliver(u, pk, P.get('received_by', ''), normalise_mobile(P.get('receiver_mobile', '')), P.get('shortage') or 0, P.get('damaged') or 0, P.get('notes', ''), file)
            msg = 'Delivery recorded.'
        elif action == 'cancel':
            L.cancel(u, pk, P.get('reason', '')); msg = 'Dispatch cancelled.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except (InvalidOperation, ValueError):
        messages.error(request, 'Enter numbers only.')
    return redirect('dispatch_detail', pk=pk)


@login_required
def dispatch_print(request, pk, doc):
    permitted(request.user, 'view_dispatch')
    if doc not in ('slip', 'pass'):
        raise Http404
    d = get_object_or_404(scope(Dispatch.objects.select_related('order__partner', 'vehicle', 'driver', 'site', 'transporter'), request.user), pk=pk)
    if doc == 'slip' and not d.gross:
        messages.error(request, 'The weighment slip prints after the gross reading.')
        return redirect('dispatch_detail', pk=pk)
    if doc == 'pass' and not hasattr(d, 'invoice'):
        messages.error(request, 'The gate pass prints after the invoice is posted.')
        return redirect('dispatch_detail', pk=pk)
    return render(request, 'erp/dispatch_print.html', {'title': f'{"Weighment slip" if doc == "slip" else "Gate pass"} · {d.number}', 'd': d, 'doc': doc,
                                                       'lines': d.lines.select_related('order_line__product'), 'weighments': d.weighments.select_related('captured_by'),
                                                       'company': Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization(),
                                                       'invoice': getattr(d, 'invoice', None), 'now': timezone.now()})


@login_required
@xframe_options_sameorigin
def dispatch_pod(request, pk):
    permitted(request.user, 'view_dispatch')
    d = get_object_or_404(scope(Dispatch.objects.all(), request.user), pk=pk)
    if not d.pod_file:
        raise Http404
    response = HttpResponse(bytes(d.pod_file), content_type=d.pod_file_type)
    response['Content-Disposition'] = f'inline; filename="POD-{d.number.replace("/", "-")}"'
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response
