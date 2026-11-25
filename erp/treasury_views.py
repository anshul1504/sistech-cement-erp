"""Treasury screens: supplier payments, bank file, bank reconciliation, GST summary, fixed assets, cash flow."""
from accounts.delegation import can_approve
import csv
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import treasury as TR
from . import gl
from .accounts_views import _range, _ctx
from .models import (SupplierPayment, Partner, Account, BankStatementLine, FixedAsset, Site, AuditEvent, PAYMENT_MODES, ASSET_CATEGORIES)
from .finance_access import permitted, require_company_finance


def _banks():
    gl.ensure_chart()
    return Account.objects.filter(is_bank=True, active=True).exclude(system_key='cash').order_by('code')


# ---------- Payments ----------

@login_required
def payments(request):
    permitted(request.user, 'view_supplierpayment')
    status = request.GET.get('status', '')
    qs = SupplierPayment.objects.select_related('payee', 'bank', 'created_by')
    if status: qs = qs.filter(status=status)
    if request.GET.get('export') == 'bank':
        permitted(request.user, 'release_supplierpayment')
        approved = SupplierPayment.objects.filter(status='approved').exclude(mode__in=['offset','refund']).select_related('payee')
        try:
            bank_csv = TR.bank_file(approved)
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
            return redirect('payments')
        response = HttpResponse(bank_csv, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="bank-payments-{timezone.localdate()}.csv"'
        AuditEvent.objects.create(actor=request.user, action='Bank payment file exported', reference='CSV', detail=f'{approved.count()} payment(s)')
        return response
    return render(request, 'erp/payments.html', {'title': 'Payments', 'payments': qs[:200], 'status': status, 'payees': TR.payees(), 'statuses': [(k,'Paid / settled' if k=='paid' else 'Failed / reversed' if k=='failed' else label) for k,label in SupplierPayment._meta.get_field('status').choices],
                                                 'approved': SupplierPayment.objects.filter(status='approved').exclude(mode__in=['offset','refund']).count(), 'can_add': request.user.has_perm('erp.add_supplierpayment')})


@login_required
def payment_new(request, payee_pk):
    permitted(request.user, 'add_supplierpayment')
    payee = get_object_or_404(Partner, pk=payee_pk)
    items = TR.open_items(payee)
    P, errors = request.POST, []
    for i in items:
        i['key'] = f'{i["kind"]}_{i["obj"].pk}'
        i['value'] = P.get(f'amt_{i["key"]}', '')
        i['picked'] = P.get(f'pick_{i["key"]}') == 'on'
    if request.method == 'POST':
        try:
            picks = {(i['kind'], i['obj'].pk): Decimal(i['value'] or abs(i['amount'])) for i in items if i['picked']}
            on = date.fromisoformat(P['date']) if P.get('date') else None
            bank = _banks().filter(pk=P.get('bank') or 0).first()
            pay = TR.propose(request.user, payee, bank, picks, P.get('mode', 'neft'), on, P.get('note', ''))
        except (InvalidOperation, ValueError):
            errors.append('Enter amounts and dates correctly.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'{pay.number} · {pay.get_mode_display()} for ₹{TR.inr(pay.cash_amount, 2)} proposed. A different user approves it.')
            return redirect('payment_detail', pk=pay.pk)
    return render(request, 'erp/payment_new.html', {'title': f'Pay / offset {payee.name}', 'payee': payee, 'items': items, 'banks': _banks(), 'modes': PAYMENT_MODES, 'errors': errors,
                                                    'post': P, 'today': timezone.localdate().isoformat(), 'total': sum((i['amount'] for i in items), Decimal('0'))})


@login_required
def payment_detail(request, pk):
    permitted(request.user, 'view_supplierpayment')
    pay = get_object_or_404(SupplierPayment.objects.select_related('payee', 'bank', 'created_by', 'approved_by', 'paid_by', 'returned_by'), pk=pk)
    u = request.user
    return render(request, 'erp/payment_detail.html', {'title': pay.number, 'pay': pay,'today':timezone.localdate().isoformat(),
                                                       'allocs': pay.allocations.select_related('supplier_invoice', 'freight_bill', 'debit_note', 'fixed_asset', 'channel_line__run'),
                                                       'history': AuditEvent.objects.filter(reference=pay.number).select_related('actor').order_by('-created_at')[:10],
                                                       'can': {'decide': pay.status == 'proposed' and can_approve(u, 'approve_supplierpayment', makers=(pay.created_by_id,)) and pay.created_by_id != u.pk,
                                                               'cancel': pay.status == 'approved' and can_approve(u, 'approve_supplierpayment', makers=(pay.created_by_id,)) and pay.created_by_id != u.pk,
                                                               'release': pay.status == 'approved' and can_approve(u, 'release_supplierpayment', makers=(pay.created_by_id,)) and pay.created_by_id != u.pk,
                                                               'refund':pay.mode=='refund', 'offset':pay.mode=='offset', 'fail': pay.status == 'paid' and pay.created_by_id!=u.pk and can_approve(u, 'release_supplierpayment', makers=(pay.created_by_id,))}})


@login_required
@require_POST
def payment_action(request, pk, action):
    require_company_finance(request.user)
    get_object_or_404(SupplierPayment, pk=pk)
    P = request.POST
    try:
        if action in ('approve', 'cancel'):
            TR.decide(request.user, pk, action == 'approve', P.get('note', '')); msg = 'Decision recorded.'
        elif action=='receive_refund':
            TR.receive_refund(request.user,pk,P.get('reference',''),P.get('note',''),date.fromisoformat(P['date']) if P.get('date') else None);msg='Vendor refund receipt recorded against approved credit.'
        elif action=='reverse_refund':
            TR.reverse_refund(request.user,pk,P.get('reason',''));msg='Vendor refund reversed; credit is open again.'
        elif action=='settle':
            TR.settle(request.user,pk,P.get('note',''));msg='Non-cash offset settled; no bank payment was created.'
        elif action=='reverse_offset':
            TR.reverse_offset(request.user,pk,P.get('reason',''));msg='Non-cash settlement reversed; source balances are open again.'
        elif action == 'release':
            TR.release(request.user, pk, P.get('reference', ''), date.fromisoformat(P['date']) if P.get('date') else None); msg = 'Payment released and posted.'
        elif action == 'fail':
            TR.fail(request.user, pk, P.get('reason', '')); msg = 'Payment marked failed; its voucher is reversed and the bills are open again.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    except ValueError:
        messages.error(request, 'Enter a valid date.')
    return redirect('payment_detail', pk=pk)


# ---------- Bank reconciliation ----------

@login_required
def bank_rec(request):
    permitted(request.user, 'view_bankstatementline')
    banks = list(_banks())
    bank = next((b for b in banks if str(b.pk) == (request.POST.get('bank') or request.GET.get('bank'))), banks[0] if banks else None)
    _, as_of = _range(request)
    if request.method == 'POST' and bank:
        try:
            if request.POST.get('action') == 'import':
                f = request.FILES.get('statement')
                if not f:
                    raise ValidationError('Choose the statement CSV file.')
                raw = f.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise ValidationError('Statement file must be under 2 MB.')
                made, skipped = TR.import_statement(request.user, bank, raw.decode('utf-8-sig', errors='replace'))
                matched = TR.auto_match(request.user, bank) if request.user.has_perm('erp.change_bankstatementline') else 0
                messages.success(request, f'{made} row(s) imported ({skipped} already there); {matched} matched automatically.')
            elif request.POST.get('action') == 'auto':
                messages.success(request, f'{TR.auto_match(request.user, bank)} row(s) matched.')
            elif request.POST.get('action') == 'match':
                get_object_or_404(BankStatementLine, pk=request.POST.get('row') or 0, bank=bank)
                line = request.POST.get('line')
                TR.match(request.user, int(request.POST['row']), int(line) if line else None)
                messages.success(request, 'Match saved.' if line else 'Match removed.')
        except (ValueError, TypeError):
            messages.error(request, 'Choose a valid statement row and ledger entry.')
        except ValidationError as e:
            messages.error(request, ' '.join(e.messages))
        return redirect(f'{request.path}?bank={bank.pk}')
    rec = TR.reconciliation(bank, as_of) if bank else None
    if rec:
        for st in rec['unmatched']:
            st.cands = list(TR.candidates(st, 10)[:5])
    return render(request, 'erp/bank_rec.html', {'title': 'Bank reconciliation', 'banks': banks, 'bank': bank, 'rec': rec, 'end': as_of,
                                                 'recent': BankStatementLine.objects.filter(bank=bank, matched_line__isnull=False).select_related('matched_line__entry')[:20] if bank else [],
                                                 'can_match': request.user.has_perm('erp.change_bankstatementline'),
                                                 'can_import': request.user.has_perm('erp.add_bankstatementline')})


# ---------- GST ----------

@login_required
def gst_summary(request):
    permitted(request.user, 'view_financials')
    today = timezone.localdate()
    try:
        month = date.fromisoformat((request.GET.get('month') or today.strftime('%Y-%m')) + '-01')
    except ValueError:
        month = today.replace(day=1)
    import calendar
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    site = Site.objects.filter(pk=request.GET.get('site') or 0).first()
    g = TR.gst_summary(month, end, site)
    if request.GET.get('export') == 'hsn':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="hsn-summary-{month:%Y-%m}.csv"'
        w = csv.writer(response)
        w.writerow(['HSN', 'UQC', 'Quantity', 'Rate %', 'Taxable value', 'Tax'])
        for code, v in g['hsn']:
            w.writerow([code, v['unit'], v['qty'], v['rate'], v['taxable'], v['tax']])
        AuditEvent.objects.create(actor=request.user, action='HSN summary exported', reference='CSV', detail=f'{month} – {end}')
        return response
    return render(request, 'erp/gst_summary.html', {'title': 'GST summary', 'g': g, 'month': month, 'end': end, 'site': site, 'sites': Site.objects.filter(active=True)})


# ---------- Fixed assets ----------

@login_required
def fixed_assets(request):
    permitted(request.user, 'view_fixedasset')
    P, errors = request.POST, []
    if request.method == 'POST':
        try:
            action = P.get('action')
            if action == 'add':
                funded = 'ap' if P.get('funded') == 'ap' else _banks().filter(pk=P.get('funded') or 0).first()
                if funded is None:
                    raise ValidationError('Choose how the asset was paid for.')
                a = TR.capitalise(request.user, {'code': P.get('code'), 'name': P.get('name'), 'category': P.get('category'), 'site': get_object_or_404(Site, pk=P.get('site') or 0),
                                                 'location': P.get('location'), 'supplier': Partner.objects.filter(pk=P.get('supplier') or 0).first(),
                                                 'put_to_use': date.fromisoformat(P.get('put_to_use')), 'cost': Decimal(P.get('cost') or 0), 'salvage': Decimal(P.get('salvage') or 0),
                                                 'method': P.get('method', 'slm'), 'life_years': Decimal(P['life_years']) if P.get('life_years') else None,
                                                 'wdv_rate': Decimal(P['wdv_rate']) if P.get('wdv_rate') else None}, funded)
                messages.success(request, f'{a} capitalised.')
            elif action == 'depreciate':
                n, total = TR.run_depreciation(request.user, date.fromisoformat(P.get('month') + '-01'))
                messages.success(request, f'Depreciation posted for {n} asset(s): ₹{TR.inr(total, 2)}.' if n else 'Nothing to depreciate for that month (already posted or no assets in use).')
            elif action == 'dispose':
                get_object_or_404(FixedAsset, pk=P.get('asset') or 0)
                a = TR.dispose(request.user, int(P.get('asset')), date.fromisoformat(P.get('on')), Decimal(P.get('proceeds') or 0), _banks().filter(pk=P.get('bank') or 0).first())
                messages.success(request, f'{a} disposed.')
            return redirect('fixed_assets')
        except (ValueError, TypeError, InvalidOperation):
            errors.append('Enter dates and amounts correctly.')
        except ValidationError as e:
            errors += e.messages
    assets = list(FixedAsset.objects.select_related('site'))
    return render(request, 'erp/fixed_assets.html', {'title': 'Fixed assets', 'assets': assets, 'errors': errors, 'post': P, 'categories': ASSET_CATEGORIES, 'banks': _banks(),
                                                     'sites': Site.objects.filter(active=True), 'suppliers': Partner.objects.filter(kind='Supplier', active=True).order_by('name'),
                                                     'totals': {'cost': sum((a.cost for a in assets if a.status == 'active'), Decimal('0')),
                                                                'book': sum((a.book_value for a in assets if a.status == 'active'), Decimal('0'))},
                                                     'month': (timezone.localdate().replace(day=1) - timezone.timedelta(days=1)).strftime('%Y-%m'),
                                                     'can_add': request.user.has_perm('erp.add_fixedasset'), 'can_dep': request.user.has_perm('erp.depreciate_fixedasset'),
                                                     'today': timezone.localdate().isoformat()})


@login_required
def cash_flow(request):
    permitted(request.user, 'view_financials')
    start, end = _range(request)
    return render(request, 'erp/cash_flow.html', _ctx(request, title='Cash flow', cf=TR.cash_flow(start, end), start=start, end=end))
