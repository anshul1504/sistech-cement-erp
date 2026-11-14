"""Quotation screens: list, form, printable quotation with GST, approval, outcome, revisions, WhatsApp share and conversion."""
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote as urlquote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import quotes as QT
from .gst import in_words
from .models import Quotation, Partner, CRMLead, Product, Site, Organization, AuditEvent, INDIAN_STATES, FREIGHT_TERMS, CUSTOMER_KINDS
from .services import permitted, scope


def _sites(user):
    qs = Site.objects.filter(active=True).order_by('code')
    return qs if user.is_superuser else qs.filter(siteaccess__user=user)


@login_required
def quotations(request):
    permitted(request.user, 'view_quotation')
    qs = scope(Quotation.objects.select_related('partner', 'lead', 'site', 'created_by').prefetch_related('lines'), request.user)
    status, q = request.GET.get('status', ''), request.GET.get('q', '').strip()
    if status: qs = qs.filter(status=status)
    else: qs = qs.exclude(status='revised')
    if q: qs = qs.filter(Q(number__icontains=q) | Q(partner__name__icontains=q) | Q(lead__business_name__icontains=q))
    base = scope(Quotation.objects.all(), request.user)
    rows = list(qs[:200])
    for r in rows:
        r.total = QT.totals(r)['grand_total']
    return render(request, 'erp/quotations.html', {'title': 'Quotations', 'rows': rows, 'status': status, 'q': q, 'statuses': Quotation._meta.get_field('status').choices,
                                                   'counts': {k: base.filter(status=k).count() for k in ('approval', 'sent', 'accepted')},
                                                   'can_add': request.user.has_perm('erp.add_quotation')})


@login_required
def quotation_form(request, pk=None):
    q = get_object_or_404(scope(Quotation.objects.all(), request.user), pk=pk) if pk else None
    permitted(request.user, 'change_quotation' if q else 'add_quotation')
    sites = list(_sites(request.user))
    customers = list(Partner.objects.filter(kind__in=CUSTOMER_KINDS, active=True).order_by('name'))
    leads = list(scope(CRMLead.objects.filter(partner__isnull=True).exclude(stage='lost'), request.user).order_by('business_name')[:300])
    products = list(Product.objects.filter(active=True, category__in=['Cement', 'TMT']).order_by('category', 'name'))
    P, errors = request.POST, []
    if q and request.method != 'POST':
        rows = [{'product': str(l.product_id), 'qty': f'{l.quantity.normalize():f}', 'rate': f'{l.rate:.2f}'} for l in q.lines.all()]
    else:
        rows = []
    rows += [{'product': P.get(f'product_{i}', ''), 'qty': P.get(f'qty_{i}', ''), 'rate': P.get(f'rate_{i}', '')} for i in range(len(rows), 10)] if request.method != 'POST' else \
        [{'product': P.get(f'product_{i}', ''), 'qty': P.get(f'qty_{i}', ''), 'rate': P.get(f'rate_{i}', '')} for i in range(10)]
    prefill_partner = request.GET.get('partner', '')
    prefill_lead = request.GET.get('lead', '')
    if request.method == 'POST':
        pmap = {str(p.pk): p for p in products}
        try:
            lines = [(pmap[r['product']], Decimal(r['qty'] or 0), Decimal(r['rate']) if r['rate'] else None) for r in rows if r['product'] in pmap and r['qty']]
            site = next((s for s in sites if str(s.pk) == P.get('site')), None)
            if not site:
                raise ValidationError('Choose the supplying plant / depot.')
            data = {'site': site, 'partner': next((c for c in customers if str(c.pk) == P.get('partner')), None), 'lead': next((l for l in leads if str(l.pk) == P.get('lead')), None),
                    'date': date.fromisoformat(P.get('date')), 'valid_until': date.fromisoformat(P.get('valid_until')), 'place_of_supply': P.get('place_of_supply', ''),
                    'freight_terms': P.get('freight_terms', 'for_destination'), 'delivery_terms': P.get('delivery_terms', '')[:150], 'payment_terms': P.get('payment_terms', '')[:150],
                    'notes': P.get('notes', '')[:300]}
            saved = QT.save(request.user, data, lines, q)
        except (ValueError, InvalidOperation):
            errors.append('Enter dates, quantities and rates correctly.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'{saved} saved. Submit it to send or for discount approval.')
            return redirect('quotation_detail', pk=saved.pk)
    today = timezone.localdate()
    defaults = {'date': (q.date if q else today).isoformat(), 'valid_until': (q.valid_until if q else today + timedelta(days=15)).isoformat(),
                'site': str(q.site_id) if q else '', 'partner': str(q.partner_id or '') if q else prefill_partner, 'lead': str(q.lead_id or '') if q else prefill_lead,
                'place_of_supply': q.place_of_supply if q else (Organization.objects.filter(pk=1).values_list('state', flat=True).first() or ''),
                'freight_terms': q.freight_terms if q else 'for_destination', 'delivery_terms': q.delivery_terms if q else '', 'payment_terms': q.payment_terms if q else '',
                'notes': q.notes if q else ''}
    vals = {k: P.get(k, v) for k, v in defaults.items()}
    return render(request, 'erp/quotation_form.html', {'title': f'Edit {q}' if q else 'New quotation', 'q': q, 'sites': sites, 'customers': customers, 'leads': leads,
                                                       'products': products, 'rows': rows, 'errors': errors, 'v': vals, 'states': INDIAN_STATES, 'freight_terms': FREIGHT_TERMS})


@login_required
def quotation_detail(request, pk):
    permitted(request.user, 'view_quotation')
    q = get_object_or_404(scope(Quotation.objects.select_related('site', 'partner', 'lead', 'created_by', 'approved_by', 'order', 'previous'), request.user), pk=pk)
    t = QT.totals(q)
    phone = ''.join(ch for ch in ((q.partner.phone if q.partner_id else q.lead.mobile) or '') if ch.isdigit())[-10:]
    org = Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization()
    msg = (f'Dear {q.customer_name},\nSISTECH quotation {q.number} (rev {q.revision}) dated {q.date:%d-%m-%Y}:\n' +
           '\n'.join(f'• {r["line"].product.name}: {r["line"].quantity.normalize():f} {r["line"].product.unit} @ ₹{r["line"].rate} + GST {r["line"].gst_rate.normalize():f}%' for r in t['rows']) +
           f'\nTotal incl. GST ₹{t["grand_total"]:,}. Valid till {q.valid_until:%d-%m-%Y}.\n— {org.trade_name or org.legal_name}')
    u = request.user
    st = q.status
    return render(request, 'erp/quotation_detail.html', {
        'title': str(q), 'q': q, 't': t, 'words': in_words(t['grand_total']), 'company': org, 'expired': st == 'sent' and q.valid_until < timezone.localdate(),
        'whatsapp': f'https://wa.me/91{phone}?text={urlquote(msg)}' if len(phone) == 10 and st in ('ready', 'sent') else '',
        'history': AuditEvent.objects.filter(reference=q.number).select_related('actor').order_by('-created_at')[:12],
        'can': {'edit': st == 'draft' and u.has_perm('erp.change_quotation'), 'submit': st == 'draft' and u.has_perm('erp.change_quotation'),
                'approve': st == 'approval' and u.has_perm('erp.approve_quotation') and q.created_by_id != u.pk,
                'send': st == 'ready' and u.has_perm('erp.change_quotation'), 'outcome': st == 'sent' and u.has_perm('erp.change_quotation'),
                'revise': st in ('approval', 'ready', 'sent', 'accepted', 'rejected', 'expired') and u.has_perm('erp.add_quotation') and not hasattr(q, 'next_revision'),
                'convert': st == 'accepted' and u.has_perm('erp.create_sales_order')}})


@login_required
@require_POST
def quotation_action(request, pk, action):
    get_object_or_404(scope(Quotation.objects.all(), request.user), pk=pk)
    P, u = request.POST, request.user
    try:
        if action == 'submit':
            q = QT.submit(u, pk); msg = 'Sent for discount approval.' if q.status == 'approval' else 'Ready to send.'
        elif action in ('approve', 'sendback'):
            QT.approve(u, pk, action == 'approve', P.get('note', '')); msg = 'Decision recorded.'
        elif action == 'sent':
            QT.mark_sent(u, pk); msg = 'Marked as sent.'
        elif action in ('accepted', 'rejected'):
            QT.outcome(u, pk, action == 'accepted', P.get('note', '')); msg = 'Outcome recorded.'
        elif action == 'revise':
            new = QT.revise(u, pk)
            messages.success(request, f'Revision {new.revision} created as a draft.')
            return redirect('quotation_detail', pk=new.pk)
        elif action == 'convert':
            order = QT.convert(u, pk)
            messages.success(request, f'Sales order {order.number} drafted from the quotation. Submit it for approval.')
            return redirect('order_detail', pk=order.pk)
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('quotation_detail', pk=pk)
