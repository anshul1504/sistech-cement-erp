"""Cost centres and budgets (SRS FR-FIN-001, FR-FIN-015).

- Cost centres are an optional analysis dimension on journal lines (manual vouchers carry them; reversals keep them).
- A budget is per financial year and versioned: draft → submitted → approved by a different user. Approving a version
  supersedes the previously approved one, so there is always at most one approved budget per year and history is kept.
- Cells are account × cost centre (optional) × month, for income and expense accounts only.
- Budget vs actual compares approved (or a chosen) version with posted journal lines: income actual = credit − debit,
  expense actual = debit − credit. Year-end carry-forward entries are excluded.
"""
import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum, Max
from django.utils import timezone

from .finance_access import require_company_finance
from .models import Budget, BudgetLine, CostCentre, Account, JournalLine, AuditEvent

ZERO = Decimal('0')
MONTHS = ['Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar']


def _need(user, perm):
    require_company_finance(user)
    if not user.has_perm('erp.' + perm):
        raise PermissionDenied


def fy_months(fy_start):
    return [date(fy_start.year + (1 if i >= 9 else 0), (4 + i - 1) % 12 + 1, 1) for i in range(12)]


def fy_of(d):
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def _log(user, action, ref, detail=''):
    AuditEvent.objects.create(actor=user, action=action, reference=ref, detail=detail[:250])


# ---------- Cost centres ----------

@transaction.atomic
def save_cost_centre(user, code, name, kind='department', site=None, active=True, pk=None):
    _need(user, 'change_costcentre' if pk else 'add_costcentre')
    code, name = (code or '').strip().upper(), (name or '').strip()
    if not code or not name:
        raise ValidationError('Enter the code and name.')
    if CostCentre.objects.filter(code=code).exclude(pk=pk).exists():
        raise ValidationError(f'Cost centre {code} already exists.')
    cc = CostCentre.objects.get(pk=pk) if pk else CostCentre()
    if pk and cc.code != code and cc.lines.exists():
        raise ValidationError('The code is used on posted entries and cannot change. Rename only.')
    cc.code, cc.name, cc.kind, cc.site, cc.active = code[:20], name[:100], kind, site, active
    cc.full_clean()
    cc.save()
    _log(user, 'Cost centre saved', cc.code, f'{cc.name} · {"active" if active else "inactive"}')
    return cc


# ---------- Budget versions ----------

def budget_accounts():
    return Account.objects.filter(type__in=['income', 'expense'], active=True).order_by('code')


@transaction.atomic
def create_budget(user, fy_start, name='Annual budget', copy_from=None):
    _need(user, 'add_budget')
    if fy_start.month != 4 or fy_start.day != 1:
        raise ValidationError('A financial year starts on 1 April.')
    if Budget.objects.filter(fy_start=fy_start, status__in=['draft', 'submitted', 'rejected']).exists():
        raise ValidationError('An unapproved version already exists for this year — finish or edit that one.')
    version = (Budget.objects.filter(fy_start=fy_start).aggregate(v=Max('version'))['v'] or 0) + 1
    b = Budget.objects.create(fy_start=fy_start, name=(name or 'Annual budget')[:80], version=version, created_by=user)
    if copy_from:
        months = dict(zip(fy_months(copy_from.fy_start), fy_months(fy_start)))
        BudgetLine.objects.bulk_create([BudgetLine(budget=b, account_id=l.account_id, cost_centre_id=l.cost_centre_id, month=months[l.month], amount=l.amount)
                                        for l in copy_from.lines.all()])
    _log(user, 'Budget drafted', str(b), f'copied from {copy_from}' if copy_from else '')
    return b


def _editable(b):
    if b.status not in ('draft', 'rejected'):
        raise ValidationError('Only a draft or sent-back budget can be edited. Create a new version to change an approved budget.')


@transaction.atomic
def set_cells(user, pk, cost_centre, cells):
    """cells: {(account_pk, month): amount}. Blank / zero removes the cell. Replaces the grid for that cost centre."""
    _need(user, 'change_budget')
    b = Budget.objects.select_for_update().get(pk=pk)
    _editable(b)
    allowed = {a.pk for a in budget_accounts()}
    months = set(fy_months(b.fy_start))
    rows = []
    for (aid, month), amount in cells.items():
        if aid not in allowed:
            raise ValidationError('Budget only active income and expense accounts.')
        if month not in months:
            raise ValidationError('Month is outside the financial year.')
        if amount is None or amount == 0:
            continue
        if amount < 0 or not amount.is_finite():
            raise ValidationError('Budget amounts must be positive numbers.')
        rows.append(BudgetLine(budget=b, account_id=aid, cost_centre=cost_centre, month=month, amount=amount.quantize(Decimal('0.01'))))
    b.lines.filter(cost_centre=cost_centre).delete()
    BudgetLine.objects.bulk_create(rows)
    _log(user, 'Budget updated', str(b), f'{cost_centre or "company"} · {len(rows)} cells')
    return b


def import_csv(user, pk, raw):
    """CSV columns: account code, cost centre code (blank = company), Apr … Mar."""
    b = Budget.objects.get(pk=pk)
    accounts = {a.code: a for a in budget_accounts()}
    centres = {c.code: c for c in CostCentre.objects.filter(active=True)}
    grids, problems = defaultdict(dict), []
    months = fy_months(b.fy_start)
    reader = csv.reader(io.StringIO(raw.lstrip('﻿')))
    for n, row in enumerate(reader, 1):
        if not row or not row[0].strip() or row[0].strip().lower() in ('account', 'account code'):
            continue
        code, cc = row[0].strip(), (row[1].strip().upper() if len(row) > 1 else '')
        if code not in accounts:
            problems.append(f'Row {n}: {code} is not an active income/expense account.'); continue
        if cc and cc not in centres:
            problems.append(f'Row {n}: cost centre {cc} not found.'); continue
        for i, m in enumerate(months):
            v = row[2 + i].strip().replace(',', '') if len(row) > 2 + i else ''
            try:
                grids[cc][(accounts[code].pk, m)] = Decimal(v) if v else None
            except InvalidOperation:
                problems.append(f'Row {n}: {MONTHS[i]} amount "{v}" is not a number.')
    if problems:
        raise ValidationError(problems[:20])
    if not grids:
        raise ValidationError('The file has no budget rows.')
    with transaction.atomic():
        for cc, cells in grids.items():
            existing = {(l.account_id, l.month): l.amount for l in b.lines.filter(cost_centre=centres.get(cc))}
            existing.update(cells)
            set_cells(user, pk, centres.get(cc), existing)
    return sum(len(c) for c in grids.values())


@transaction.atomic
def submit(user, pk):
    _need(user, 'change_budget')
    b = Budget.objects.select_for_update().get(pk=pk)
    _editable(b)
    if not b.lines.exists():
        raise ValidationError('Enter budget amounts before submitting.')
    b.status = 'submitted'
    b.save(update_fields=['status'])
    _log(user, 'Budget submitted', str(b))
    return b


@transaction.atomic
def decide(user, pk, approve, note=''):
    _need(user, 'approve_budget')
    b = Budget.objects.select_for_update().get(pk=pk)
    if b.status != 'submitted':
        raise ValidationError('Only a submitted budget can be decided.')
    if b.created_by_id == user.pk:
        raise ValidationError('A different user must approve the budget (maker-checker).')
    if not approve and not (note or '').strip():
        raise ValidationError('Say what must change.')
    if approve:
        Budget.objects.filter(fy_start=b.fy_start, status='approved').update(status='superseded')
    b.status, b.decided_by, b.decided_at, b.decision_note = ('approved' if approve else 'rejected'), user, timezone.now(), (note or '').strip()[:200]
    b.save()
    _log(user, 'Budget approved' if approve else 'Budget sent back', str(b), note)
    return b


# ---------- Budget vs actual ----------

def comparison(budget, upto, cost_centre=None, site=None):
    """Rows per account: full-year budget, budget to date (months up to `upto`), actual to date, variance (favourable +)."""
    months = [m for m in fy_months(budget.fy_start) if m <= upto.replace(day=1)]
    end = upto
    lines = budget.lines.all()
    if cost_centre is not None:
        lines = lines.filter(cost_centre=cost_centre)
    year = {r['account']: r['t'] for r in lines.values('account').annotate(t=Sum('amount'))}
    todate = {r['account']: r['t'] for r in lines.filter(month__in=months).values('account').annotate(t=Sum('amount'))}
    act = JournalLine.objects.filter(entry__status='posted', entry__date__gte=budget.fy_start, entry__date__lte=end, account__type__in=['income', 'expense']).exclude(entry__kind='yearend')
    if cost_centre is not None:
        act = act.filter(cost_centre=cost_centre)
    if site is not None:
        act = act.filter(entry__site=site)
    actual = {r['account']: (r['d'] or ZERO) - (r['c'] or ZERO) for r in act.values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    accounts = Account.objects.in_bulk(set(year) | set(actual))
    groups = defaultdict(list)
    for aid, a in accounts.items():
        sign = -1 if a.type == 'income' else 1
        actual_v = sign * actual.get(aid, ZERO)
        bud = todate.get(aid, ZERO)
        var = (actual_v - bud) if a.type == 'income' else (bud - actual_v)   # favourable is positive
        groups[(a.type, a.group)].append({'account': a, 'year': year.get(aid, ZERO), 'budget': bud, 'actual': actual_v, 'variance': var,
                                          'pct': (actual_v * 100 / bud).quantize(Decimal('0.1')) if bud else None})
    out = []
    for (typ, grp), rows in sorted(groups.items(), key=lambda kv: (kv[0][0] != 'income', kv[0][1])):
        rows.sort(key=lambda r: r['account'].code)
        out.append({'type': typ, 'group': grp, 'rows': rows, 'budget': sum((r['budget'] for r in rows), ZERO), 'actual': sum((r['actual'] for r in rows), ZERO),
                    'variance': sum((r['variance'] for r in rows), ZERO), 'year': sum((r['year'] for r in rows), ZERO)})
    tot = lambda typ, k: sum((g[k] for g in out if g['type'] == typ), ZERO)
    summary = {k: {'income': tot('income', k), 'expense': tot('expense', k), 'profit': tot('income', k) - tot('expense', k)} for k in ('budget', 'actual', 'year')}
    return {'groups': out, 'summary': summary, 'months': months}
