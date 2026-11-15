"""Accounts screens: journals and vouchers, trial balance, profit & loss, balance sheet, account ledger, chart of accounts, periods."""
from accounts.delegation import can_approve
import csv
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import gl, journal_reversals as JR
from .gst import financial_year, fy_start_month
from .models import Account, JournalEntry, FinancialPeriod, Partner, Site, AuditEvent, ACCOUNT_TYPES
from .finance_access import permitted, require_company_finance


def _range(request):
    """Default: current financial year to date."""
    today = timezone.localdate()
    fy = financial_year(today)
    fy_start = date(int(fy[:4]), fy_start_month(), 1)
    try:
        start = date.fromisoformat(request.GET['from']) if request.GET.get('from') else fy_start
        end = date.fromisoformat(request.GET['to']) if request.GET.get('to') else today
    except ValueError:
        start, end = fy_start, today
    return (start, end) if start <= end else (end, start)


def _site(request):
    value = request.GET.get('site')
    if not value:
        return None
    if not value.isdigit():
        raise Http404
    return get_object_or_404(Site, pk=value)


def _ctx(request, **extra):
    return {'sites': Site.objects.filter(active=True).order_by('code'), 'site': _site(request), **extra}


@login_required
def journals(request):
    permitted(request.user, 'view_journalentry')
    start, end = _range(request)
    qs = JournalEntry.objects.filter(date__range=(start, end)).select_related('site', 'created_by').prefetch_related('lines')
    kind, status, q = request.GET.get('kind', ''), request.GET.get('status', ''), request.GET.get('q', '').strip()
    if kind: qs = qs.filter(kind=kind)
    if status: qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(number__icontains=q) | Q(narration__icontains=q) | Q(source__icontains=q))
    if _site(request): qs = qs.filter(site=_site(request))
    drafts = JournalEntry.objects.filter(kind='manual', status='draft').count()
    return render(request, 'erp/journals.html', _ctx(request, title='Accounts & ledgers', page=Paginator(qs, 50).get_page(request.GET.get('page')), start=start, end=end,
                                                     kind=kind, status=status, q=q, drafts=drafts, can_add=request.user.has_perm('erp.add_journalentry')))


@login_required
def journal_detail(request, pk):
    permitted(request.user, 'view_journalentry')
    je = get_object_or_404(JournalEntry.objects.select_related('site', 'created_by', 'approved_by', 'reversal_of'), pk=pk)
    u = request.user
    requests=list(je.reversal_requests.select_related('created_by','reviewed_by','reversal','entry'))
    for item in requests:item.can_decide=JR.can_decide(u,item)
    from .journal_sources import document_link
    return render(request, 'erp/journal_detail.html', {'title': je.number, 'je': je, 'source_document': document_link(je, u), 'lines': je.lines.select_related('account', 'partner'),
                                                       'reversed_by': getattr(je, 'reversed_by', None),
                                                       'can_decide': je.kind == 'manual' and je.status == 'draft' and can_approve(u, 'approve_journalentry', makers=(je.created_by_id,)) and je.created_by_id != u.pk,
                                                       'can_reverse': JR.can_request(u,je),'reversal_requests':requests,'reversal_key':uuid.uuid4()})


@login_required
@require_POST
def journal_action(request, pk, action):
    require_company_finance(request.user)
    get_object_or_404(JournalEntry, pk=pk)
    try:
        if action in ('approve', 'reject'):
            je = gl.decide_voucher(request.user, pk, action == 'approve', request.POST.get('note', ''))
            messages.success(request, f'{je.number} {je.get_status_display().lower()}.')
        elif action == 'reverse':
            on=date.fromisoformat(request.POST['date']) if request.POST.get('date') else None
            JR.propose(request.user,pk,on,request.POST.get('reason',''),request.POST.get('request_key'))
            messages.success(request,'Reversal requested. An independent reviewer must approve before posting.')
        elif action in ('approve_reversal','reject_reversal','cancel_reversal'):
            from .models import JournalReversalRequest
            raw=request.POST.get('reversal_request','')
            item=get_object_or_404(JournalReversalRequest,pk=raw if raw.isascii() and raw.isdecimal() else 0,entry_id=pk)
            if action=='cancel_reversal':JR.cancel(request.user,item.pk,request.POST.get('note',''))
            else:JR.decide(request.user,item.pk,action=='approve_reversal',request.POST.get('note',''))
            messages.success(request,'Reversal request decision recorded.')
        else:
            raise Http404
    except ValueError:
        messages.error(request, 'Enter a valid reversal date.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('journal_detail', pk=pk)


@login_required
def voucher_new(request):
    permitted(request.user, 'add_journalentry')
    accounts = list(Account.objects.filter(active=True, is_control=False).order_by('code'))
    partners = list(Partner.objects.filter(active=True).order_by('name'))
    P, errors = request.POST, []
    rows = [{k: P.get(f'{k}_{i}', '') for k in ('account', 'partner', 'debit', 'credit', 'narr')} for i in range(10)]
    if request.method == 'POST':
        amap, pmap = {str(a.pk): a for a in accounts}, {str(p.pk): p for p in partners}
        try:
            on = date.fromisoformat(P.get('date', ''))
            lines = []
            for r in rows:
                if r['account'] in amap and (r['debit'] or r['credit']):
                    lines.append((amap[r['account']], Decimal(r['debit'] or 0), Decimal(r['credit'] or 0), pmap.get(r['partner']), r['narr']))
            je = gl.create_voucher(request.user, on, Site.objects.filter(pk=P.get('site') or 0).first(), P.get('narration', ''), lines)
        except (ValueError, InvalidOperation):
            errors.append('Enter a valid date and amounts.')
        except ValidationError as e:
            errors += e.messages
        else:
            messages.success(request, f'{je.number} saved for approval. A different user approves it.')
            return redirect('journal_detail', pk=je.pk)
    return render(request, 'erp/voucher_new.html', _ctx(request, title='Journal voucher', accounts=accounts, partners=partners, rows=rows, errors=errors, post=P,
                                                        today=timezone.localdate().isoformat()))


@login_required
def trial_balance(request):
    permitted(request.user, 'view_financials')
    start, end = _range(request)
    tb = gl.trial_balance(start, end, _site(request))
    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="trial-balance-{end}.csv"'
        w = csv.writer(response)
        w.writerow(['Code', 'Account', 'Group', 'Opening', 'Debit', 'Credit', 'Closing'])
        for r in tb['rows']:
            w.writerow([r['account'].code, r['account'].name, r['account'].group, r['opening'], r['debit'], r['credit'], r['closing']])
        AuditEvent.objects.create(actor=request.user, action='Trial balance exported', reference='CSV', detail=f'{start} – {end}')
        return response
    return render(request, 'erp/trial_balance.html', _ctx(request, title='Trial balance', tb=tb, start=start, end=end, checks=gl.subledger_check(end)))


@login_required
def profit_loss(request):
    permitted(request.user, 'view_financials')
    start, end = _range(request)
    return render(request, 'erp/profit_loss.html', _ctx(request, title='Profit & loss', pl=gl.profit_and_loss(start, end, _site(request)), start=start, end=end))


@login_required
def balance_sheet(request):
    permitted(request.user, 'view_financials')
    _, end = _range(request)
    return render(request, 'erp/balance_sheet.html', _ctx(request, title='Balance sheet', bs=gl.balance_sheet(end, _site(request)), end=end))


@login_required
def account_ledger(request, pk):
    permitted(request.user, 'view_financials')
    a = get_object_or_404(Account, pk=pk)
    start, end = _range(request)
    partner = Partner.objects.filter(pk=request.GET.get('partner') or 0).first()
    return render(request, 'erp/account_ledger.html', _ctx(request, title=f'{a.code} {a.name}', a=a, led=gl.account_ledger(a, start, end, partner, _site(request)),
                                                           start=start, end=end, partner=partner,
                                                           partners=Partner.objects.filter(journal_lines__account=a).distinct().order_by('name')))


@login_required
def chart(request):
    permitted(request.user, 'view_account')
    gl.ensure_chart()
    errors = []
    if request.method == 'POST':
        permitted(request.user, 'add_account')
        P = request.POST
        code, name, typ, group = P.get('code', '').strip(), P.get('name', '').strip(), P.get('type', ''), P.get('group', '').strip()
        if not (code.isdigit() and 3 <= len(code) <= 10): errors.append('Account code is 3–10 digits.')
        if not name or not group: errors.append('Enter the name and the Schedule III group.')
        if typ not in dict(ACCOUNT_TYPES): errors.append('Choose the account type.')
        if Account.objects.filter(code=code).exists(): errors.append(f'Code {code} is already used.')
        if not errors:
            a = Account.objects.create(code=code, name=name[:100], type=typ, group=group[:60], is_bank=P.get('is_bank') == 'on')
            AuditEvent.objects.create(actor=request.user, action='Account added', reference=a.code, detail=f'{a.name} · {a.get_type_display()} · {a.group}')
            messages.success(request, f'{a} added.')
            return redirect('chart')
    bal = {r['account']: r['d'] - r['c'] for r in gl._posted().values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    accounts = list(Account.objects.all())
    for a in accounts:
        a.balance = bal.get(a.pk, Decimal('0'))
    return render(request, 'erp/chart.html', {'title': 'Chart of accounts', 'accounts': accounts, 'types': ACCOUNT_TYPES, 'groups': gl.GROUP_ORDER, 'errors': errors,
                                              'can_add': request.user.has_perm('erp.add_account')})


@login_required
def periods(request):
    permitted(request.user, 'view_financials')
    today = timezone.localdate()
    for i in range(12):
        y, m = divmod(today.year * 12 + today.month - 1 - i, 12)
        gl.period_for(date(y, m + 1, 1))
    rows = list(FinancialPeriod.objects.select_related('closed_by')[:15])
    for p in rows:
        p.vouchers = JournalEntry.objects.filter(date__range=(p.start, p.end), status='posted').count()
        p.drafts = JournalEntry.objects.filter(date__range=(p.start, p.end), status='draft').count()
    return render(request, 'erp/periods.html', {'title': 'Periods & close', 'periods': rows, 'checks': gl.subledger_check(), 'can_close': can_approve(request.user, 'close_period')})


@login_required
@require_POST
def period_action(request, pk, action):
    require_company_finance(request.user)
    if action not in ('close', 'reopen'):
        raise Http404
    p = get_object_or_404(FinancialPeriod, pk=pk)
    try:
        gl.set_period(request.user, p.start, action == 'close', request.POST.get('reason', ''))
        messages.success(request, f'{p} {"closed" if action == "close" else "reopened"}.')
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('period_detail', pk=pk)
