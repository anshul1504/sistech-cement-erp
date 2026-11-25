"""General ledger engine (SRS §5.10, §9; FR-FIN-001…004, 009, 011, 017).

Every business document posts itself here through one function, `post`, which:
- refuses unbalanced vouchers (total debit must equal total credit to the paisa);
- refuses dates in a closed accounting period;
- is idempotent: one voucher per source document (e.g. 'inv:12'), so retries never double-post;
- never edits a posted voucher — corrections are reversing entries linked to the original (FR-FIN-017).

Posting map (illustrative SRS §9.2; finance should confirm the accounts):
  Tax invoice        Dr Trade receivables (customer)      Cr Sales by product line, Cr Output CGST/SGST/IGST, ± Round off
  Receipt            Dr Bank / Cash                        Cr Trade receivables (customer)        (bounce = reversal)
  Credit note        Dr Sales returns, Dr Output GST       Cr Trade receivables (customer)
  GRN                Dr Inventory                          Cr Goods received not invoiced (GRNI)
  Supplier invoice   Dr GRNI (matched), Dr/Cr Price variance, Dr Input GST    Cr Trade payables (supplier)
  Debit note         Dr Trade payables (supplier)          Cr GRNI (return) or Price variance, Cr Input GST
  Freight bill       Dr Freight outward                    Cr Trade payables — transporters
  Stock movements    COGS on dispatch, WIP on consumption / production, transit on transfers, scrap, count adjustment
"""
from accounts.delegation import authorize, can_approve
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from .gst import next_number, inr
from .finance_access import require_company_finance
from .models import Account, FinancialPeriod, JournalEntry, JournalLine, AuditEvent

ZERO = Decimal('0')
CENT = Decimal('0.01')

# code, name, type, Schedule III group, system key, control, bank/cash
CHART = [
    ('1100', 'Cash in hand', 'asset', 'Cash and cash equivalents', 'cash', False, True),
    ('1110', 'Bank — main current account', 'asset', 'Cash and cash equivalents', 'bank_main', False, True),
    ('1200', 'Trade receivables', 'asset', 'Trade receivables', 'ar', True, False),
    ('1300', 'Raw material & fuel', 'asset', 'Inventories', 'inventory_rm', True, False),
    ('1310', 'Stores, spares & packing', 'asset', 'Inventories', 'inventory_stores', True, False),
    ('1320', 'Finished goods', 'asset', 'Inventories', 'inventory_fg', True, False),
    ('1330', 'Work in progress', 'asset', 'Inventories', 'wip', True, False),
    ('1340', 'Stock in transit', 'asset', 'Inventories', 'stock_transit', True, False),
    ('1400', 'Input CGST', 'asset', 'Other current assets', 'input_cgst', True, False),
    ('1410', 'Input SGST', 'asset', 'Other current assets', 'input_sgst', True, False),
    ('1420', 'Input IGST', 'asset', 'Other current assets', 'input_igst', True, False),
    ('1500', 'Advances to suppliers', 'asset', 'Short-term loans and advances', 'supplier_advance', False, False),
    ('1600', 'Plant & machinery', 'asset', 'Property, plant and equipment', 'fa_plant', False, False),
    ('1610', 'Buildings', 'asset', 'Property, plant and equipment', 'fa_building', False, False),
    ('1620', 'Vehicles', 'asset', 'Property, plant and equipment', 'fa_vehicle', False, False),
    ('1630', 'Furniture & office equipment', 'asset', 'Property, plant and equipment', 'fa_office', False, False),
    ('1690', 'Accumulated depreciation', 'asset', 'Property, plant and equipment', 'acc_depreciation', True, False),
    ('2100', 'Trade payables — suppliers', 'liability', 'Trade payables', 'ap', True, False),
    ('2110', 'Trade payables — transporters', 'liability', 'Trade payables', 'ap_freight', True, False),
    ('2120', 'Goods received not invoiced', 'liability', 'Other current liabilities', 'grni', True, False),
    ('2200', 'Output CGST', 'liability', 'Duties and taxes', 'output_cgst', True, False),
    ('2210', 'Output SGST', 'liability', 'Duties and taxes', 'output_sgst', True, False),
    ('2220', 'Output IGST', 'liability', 'Duties and taxes', 'output_igst', True, False),
    ('2300', 'Customer advances', 'liability', 'Other current liabilities', 'customer_advance', False, False),
    ('2310', 'Scheme incentives payable', 'liability', 'Other current liabilities', 'scheme_payable', True, False),
    ('2320', 'Sales commission payable', 'liability', 'Other current liabilities', 'commission_payable', False, False),
    ('2330', 'TDS payable', 'liability', 'Duties and taxes', 'tds_payable', False, False),
    ('2340', 'Dealer security deposits', 'liability', 'Other current liabilities', 'security_deposits', True, False),
    ('3100', 'Share capital', 'equity', 'Equity share capital', 'capital', False, False),
    ('3200', 'Opening balance equity', 'equity', 'Other equity', 'opening_equity', False, False),
    ('3300', 'Retained earnings', 'equity', 'Other equity', 'retained', False, False),
    ('4100', 'Sales — cement', 'income', 'Revenue from operations', 'sales_cement', False, False),
    ('4110', 'Sales — TMT bars', 'income', 'Revenue from operations', 'sales_tmt', False, False),
    ('4120', 'Sales — other', 'income', 'Revenue from operations', 'sales_other', False, False),
    ('4190', 'Sales returns & allowances', 'income', 'Revenue from operations', 'sales_returns', False, False),
    ('4195', 'Scheme discounts & dealer incentives', 'income', 'Revenue from operations', 'sales_schemes', False, False),
    ('4900', 'Round off', 'income', 'Other income', 'round_off', False, False),
    ('4910', 'Gain on sale of assets', 'income', 'Other income', 'asset_gain', False, False),
    ('5100', 'Cost of goods sold', 'expense', 'Cost of materials consumed', 'cogs', False, False),
    ('5110', 'Purchase price variance', 'expense', 'Cost of materials consumed', 'ppv', False, False),
    ('5120', 'Stock count adjustment', 'expense', 'Other expenses', 'stock_adjustment', False, False),
    ('5130', 'Scrap & damage loss', 'expense', 'Other expenses', 'scrap_loss', False, False),
    ('5140', 'Transit loss', 'expense', 'Other expenses', 'transit_loss', False, False),
    ('5200', 'Freight outward', 'expense', 'Other expenses', 'freight_out', False, False),
    ('5300', 'Power & fuel', 'expense', 'Other expenses', 'power', False, False),
    ('5400', 'Salaries & wages', 'expense', 'Employee benefits expense', 'salaries', False, False),
    ('5500', 'Repairs & maintenance', 'expense', 'Other expenses', 'repairs', False, False),
    ('5600', 'Bank charges', 'expense', 'Finance costs', 'bank_charges', False, False),
    ('5700', 'Depreciation', 'expense', 'Depreciation and amortisation', 'depreciation', False, False),
    ('5800', 'Sales commission', 'expense', 'Other expenses', 'commission', False, False),
    ('5710', 'Loss on sale of assets', 'expense', 'Other expenses', 'asset_loss', False, False),
]
GROUP_ORDER = ['Equity share capital', 'Other equity', 'Trade payables', 'Other current liabilities', 'Duties and taxes', 'Property, plant and equipment',
               'Inventories', 'Trade receivables', 'Cash and cash equivalents', 'Short-term loans and advances', 'Other current assets',
               'Revenue from operations', 'Other income', 'Cost of materials consumed', 'Employee benefits expense', 'Finance costs',
               'Depreciation and amortisation', 'Other expenses']


def ensure_chart():
    made = 0
    for code, name, typ, group, key, control, bank in CHART:
        if Account.objects.filter(system_key=key).exists():
            continue
        _, created = Account.objects.get_or_create(code=code, defaults={'name': name, 'type': typ, 'group': group, 'system_key': key, 'is_control': control, 'is_bank': bank})
        made += created
    return made


def acct(key):
    a = Account.objects.filter(system_key=key).first()
    if not a:
        ensure_chart()
        a = Account.objects.get(system_key=key)
    return a


def inventory_account(product):
    if product.category in ('Cement', 'TMT'):
        return acct('inventory_fg')
    if product.category in ('Packaging', 'Spares'):
        return acct('inventory_stores')
    return acct('inventory_rm')


def sales_account(product):
    return acct({'Cement': 'sales_cement', 'TMT': 'sales_tmt'}.get(product.category, 'sales_other'))


# ---------- Periods ----------

def period_for(on):
    start = on.replace(day=1)
    end = on.replace(day=calendar.monthrange(on.year, on.month)[1])
    p, _ = FinancialPeriod.objects.get_or_create(start=start, defaults={'end': end})
    return p


def lock_finance():
    from .models import FinancePostingLock
    FinancePostingLock.objects.get_or_create(pk=1)
    return FinancePostingLock.objects.select_for_update().get(pk=1)


def check_open(on):
    lock_finance()
    cutoff = FinancialPeriod.objects.filter(status='closed', end__gte=on).order_by('-end').first()
    if cutoff:
        raise ValidationError(f'{cutoff} is closed for posting. Reopen that month before changing earlier books.')
    period = period_for(on)
    p = FinancialPeriod.objects.select_for_update().get(pk=period.pk)
    if p and p.status == 'closed':
        raise ValidationError(f'{p} is closed for posting. Date the entry in an open period or ask finance to reopen {p}.')


def set_period(user, start, close, reason=''):
    from .closing import decide_period
    return decide_period(user, start, close, reason)


# ---------- Posting ----------

def _clean(lines):
    """lines: [(account, debit, credit, partner, narration[, cost_centre])] → merged, rounded, non-zero, one-sided lines."""
    merged = defaultdict(lambda: [ZERO, ZERO, ''])
    for line in lines:
        a, dr, cr, partner, narr = line[:5]
        cc = line[5] if len(line) > 5 else None
        if not all(Decimal(v or 0).is_finite() for v in (dr, cr)):
            raise ValidationError('Journal amounts must be finite numbers.')
        k = (a.pk, partner.pk if partner else None, cc.pk if cc else None)
        merged[k][0] += Decimal(dr or 0)
        merged[k][1] += Decimal(cr or 0)
        merged[k][2] = merged[k][2] or (narr or '')
    out = []
    for (aid, pid, ccid), (dr, cr, narr) in merged.items():
        net = (dr - cr).quantize(CENT)
        if net:
            out.append((aid, pid, net if net > 0 else ZERO, -net if net < 0 else ZERO, narr[:150], ccid))
    return out


@transaction.atomic
def post(user, source, on, site, narration, lines, kind='auto', status='posted', reversal_of=None, year_close=None):
    lock_finance()
    if source:
        existing = JournalEntry.objects.filter(source=source).first()
        if existing:
            return existing
    clean = _clean(lines)
    dr = sum((l[2] for l in clean), ZERO)
    cr = sum((l[3] for l in clean), ZERO)
    if dr != cr:
        raise ValidationError(f'Voucher does not balance: debit ₹{inr(dr)} vs credit ₹{inr(cr)}.')
    if not clean:
        return None
    if kind == 'yearend':
        require_company_finance(user)
        if not can_approve(user, 'approve_yearclose', makers=(year_close.prepared_by_id, year_close.reopen_requested_by_id) if year_close else ()):
            raise PermissionDenied
        if not year_close or year_close.status not in ('proposed', 'reopen_requested') or on != year_close.end:
            raise ValidationError('Year-end entries require the independent year-close workflow.')
    else:
        check_open(on)
    period_for(on)
    je = JournalEntry.objects.create(number=next_number('JV', site, on), date=on, site=site, source=source, kind=kind, status=status, narration=narration[:250],
                                     reversal_of=reversal_of, year_close=year_close, created_by=user, approved_by=user if kind in ('auto', 'yearend') else None)
    for aid, pid, d, c, narr, ccid in clean:
        JournalLine.objects.create(entry=je, account_id=aid, partner_id=pid, debit=d, credit=c, narration=narr, cost_centre_id=ccid)
    return je


@transaction.atomic
def reverse(user, entry, on=None, reason=''):
    entry=JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.kind=='manual':raise ValidationError('Manual vouchers require the independently reviewed reversal-request workflow.')
    return _reverse_entry(user,entry,on,reason)


@transaction.atomic
def _reverse_entry(user, entry, on=None, reason=''):
    """Ledger primitive: manual callers use journal_reversals.decide after approval."""
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.kind == 'yearend':
        raise ValidationError('Use the year-end reopening workflow to reverse carry-forward entries.')
    if entry.status != 'posted':
        raise ValidationError('Only posted vouchers can be reversed.')
    if hasattr(entry, 'reversed_by'):
        raise ValidationError(f'{entry.number} has already been reversed by {entry.reversed_by.number}.')
    lines = [(l.account, l.credit, l.debit, l.partner, l.narration, l.cost_centre) for l in entry.lines.select_related('account', 'partner', 'cost_centre')]
    return post(user, f'rev:{entry.pk}', on or timezone.localdate(), entry.site, f'Reversal of {entry.number}' + (f' — {reason}' if reason else ''), lines,
                kind=entry.kind, reversal_of=entry)


def reverse_source(user, source, on=None, reason=''):
    je = JournalEntry.objects.filter(source=source, status='posted').first()
    if je and not hasattr(je, 'reversed_by'):
        return reverse(user, je, on, reason)
    return None


# ---------- Document postings ----------

def _tax_lines(prefix, cgst, sgst, igst, side):
    """side 'cr' for output tax on sales, 'dr' for input tax / reversal."""
    out = []
    for key, amt in (('cgst', cgst), ('sgst', sgst), ('igst', igst)):
        if amt:
            a = acct(f'{prefix}_{key}')
            out.append((a, amt, 0, None, '') if side == 'dr' else (a, 0, amt, None, ''))
    return out


def _round(amount):
    if not amount:
        return []
    return [(acct('round_off'), 0, amount, None, 'Round off')] if amount > 0 else [(acct('round_off'), -amount, 0, None, 'Round off')]


def post_invoice(user, inv):
    lines = [(acct('ar'), inv.grand_total, 0, inv.partner, inv.number)]
    for l in inv.lines.select_related('product'):
        lines.append((sales_account(l.product), 0, l.taxable, None, ''))
    lines += _tax_lines('output', inv.cgst, inv.sgst, inv.igst, 'cr') + _round(inv.round_off)
    return post(user, f'inv:{inv.pk}', inv.date, inv.site, f'Tax invoice {inv.number} · {inv.partner.name}', lines)


def post_credit_note(user, cn):
    lines = [(acct('sales_returns'), cn.taxable, 0, None, ''), (acct('ar'), 0, cn.total, cn.partner, cn.number)]
    lines += _tax_lines('output', cn.cgst, cn.sgst, cn.igst, 'dr') + _round(-cn.round_off)
    return post(user, f'cn:{cn.pk}', cn.date, cn.invoice.site, f'Credit note {cn.number} · {cn.partner.name}', lines)


def post_receipt(user, r):
    bank = acct('cash' if r.mode == 'cash' else 'bank_main')
    return post(user, f'rct:{r.pk}', r.date, None, f'Receipt {r.number} · {r.partner.name} · {r.get_mode_display()} {r.reference}',
                [(bank, r.amount, 0, None, r.reference), (acct('ar'), 0, r.amount, r.partner, r.number)])


def _input_tax(site, partner, tax):
    if not tax:
        return []
    interstate = bool(site.state and partner.state and site.state != partner.state)
    if interstate:
        return [(acct('input_igst'), tax, 0, None, '')]
    half = (Decimal(tax) / 2).quantize(CENT)
    return [(acct('input_cgst'), half, 0, None, ''), (acct('input_sgst'), Decimal(tax) - half, 0, None, '')]


def post_supplier_invoice(user, inv):
    """Posted when the invoice becomes payable (matched, or approved after an exception)."""
    # Clear received inventory at the PO cost, independently of the billed rate.
    # Rate discounts/premiums belong in price variance, never as stranded GRNI.
    received_cost = sum(((min(l.quantity, l.open_qty) * l.po_rate).quantize(CENT) for l in inv.lines.all()), ZERO)
    variance = inv.billed_taxable - received_cost
    lines = [(acct('grni'), received_cost, 0, None, ''), (acct('ap'), 0, inv.billed_total, inv.supplier, inv.invoice_number)]
    if variance:
        lines.append((acct('ppv'), variance, 0, None, 'Billed above PO / GRN') if variance > 0 else (acct('ppv'), 0, -variance, None, 'Billed below PO / GRN'))
    lines += _input_tax(inv.site, inv.supplier, inv.billed_tax)
    lines += _round(-(inv.billed_total - inv.billed_taxable - inv.billed_tax))
    return post(user, f'sinv:{inv.pk}', inv.invoice_date, inv.site, f'Supplier invoice {inv.invoice_number} · {inv.supplier.name}', lines)


def post_debit_note(user, dn):
    credit = acct('grni') if dn.reason == 'return' else acct('ppv')
    lines = [(acct('ap'), dn.total, 0, dn.supplier, dn.number), (credit, 0, dn.taxable, None, dn.get_reason_display())]
    lines += [(a, c, d, p, n) for a, d, c, p, n in _input_tax(dn.site, dn.supplier, dn.tax)]
    lines += _round(dn.total - dn.taxable - dn.tax)
    return post(user, f'dn:{dn.pk}', dn.date, dn.site, f'Debit note {dn.number} · {dn.supplier.name}', lines)


def post_freight_bill(user, bill):
    if not bill.payable:
        return None
    return post(user, f'frb:{bill.pk}', bill.bill_date or timezone.localdate(), None, f'Freight bill {bill.bill_number} · {bill.transporter.name}',
                [(acct('freight_out'), bill.payable, 0, None, ''), (acct('ap_freight'), 0, bill.payable, bill.transporter, bill.bill_number)])


STOCK_MAP = {  # kind: (contra account key when stock comes IN, contra key when stock goes OUT)
    'Opening': ('opening_equity', 'opening_equity'), 'Receipt': ('grni', 'grni'), 'Production': ('wip', 'wip'), 'Consumption': ('wip', 'wip'),
    'Dispatch': ('cogs', 'cogs'), 'Return': ('cogs', 'cogs'), 'PurchaseReturn': ('grni', 'grni'), 'TransferOut': ('stock_transit', 'stock_transit'),
    'TransferIn': ('stock_transit', 'stock_transit'), 'Scrap': ('scrap_loss', 'scrap_loss'), 'Adjustment': ('stock_adjustment', 'stock_adjustment'),
}


def post_stock(user, m):
    """Value every stock movement at its unit cost. State moves within a site (damage / recovery) do not change the ledger."""
    if m.kind not in STOCK_MAP:
        return None
    value = abs(m.value)
    if not value:
        return None
    inv_acct = inventory_account(m.product)
    contra = acct(STOCK_MAP[m.kind][0 if m.quantity > 0 else 1])
    lines = [(inv_acct, value, 0, None, ''), (contra, 0, value, None, '')] if m.quantity > 0 else [(contra, value, 0, None, ''), (inv_acct, 0, value, None, '')]
    if m.kind == 'PurchaseReturn' and m.quantity < 0 and m.source.startswith('dn:'):
        from .models import DebitNoteLine
        parts = m.source.split(':')
        line = DebitNoteLine.objects.filter(note_id=parts[1], grn_line_id=parts[-1], from_stock=True).first()
        if line:
            po_value = (abs(m.quantity) * line.rate).quantize(CENT)
            lines = [(contra, po_value, 0, None, 'Returned quantity at PO cost'), (inv_acct, 0, value, None, '')]
            difference = value - po_value
            if difference:
                lines.append((acct('ppv'), difference if difference > 0 else 0, -difference if difference < 0 else 0, None, 'Return valuation difference'))
    return post(user, f'stk:{m.pk}', timezone.localdate(m.created_at), m.site, f'{m.get_kind_display()} · {m.product.code} {m.quantity.normalize():f} {m.uom} · {m.source}', lines)


def post_transit_loss(user, t, value):
    if not value:
        return None
    return post(user, f'trfloss:{t.pk}', timezone.localdate(), t.to_site, f'Transit loss on {t.number}',
                [(acct('transit_loss'), value, 0, None, t.resolution[:150]), (acct('stock_transit'), 0, value, None, '')])


# ---------- Manual journal vouchers ----------

@transaction.atomic
def create_voucher(user, on, site, narration, lines):
    """lines: [(account, debit, credit, partner, narration)]. Saved as draft; a different user approves (posts) it."""
    require_company_finance(user)
    if not user.has_perm('erp.add_journalentry'):
        raise PermissionDenied
    problems = []
    if not (narration or '').strip():
        problems.append('Write the narration (why this entry is passed).')
    for line in lines:
        a, d, c, p, n = line[:5]
        cc = line[5] if len(line) > 5 else None
        if cc is not None and not cc.active:
            problems.append(f'Cost centre {cc} is inactive.')
        if not all(Decimal(v or 0).is_finite() for v in (d, c)):
            raise ValidationError('Amounts must be finite numbers.')
        if not a.active:
            problems.append(f'{a} is inactive.')
        if a.is_control:
            problems.append(f'{a} is a control account — it is posted only from its subledger documents.')
        if d and c:
            problems.append(f'{a}: a line is either debit or credit.')
        if (d or 0) < 0 or (c or 0) < 0:
            problems.append(f'{a}: amounts cannot be negative.')
    if len([l for l in lines if l[1] or l[2]]) < 2:
        problems.append('A voucher needs at least two lines.')
    if problems:
        raise ValidationError(problems)
    je = post(user, None, on, site, narration.strip(), lines, kind='manual', status='draft')
    if je is None:
        raise ValidationError('Enter amounts.')
    AuditEvent.objects.create(actor=user, site=site, action='Journal voucher drafted', reference=je.number, detail=f'₹{inr(je.total)} · {narration[:120]}')
    return je


@transaction.atomic
def decide_voucher(user, pk, approve, note=''):
    require_company_finance(user)
    je = JournalEntry.objects.select_for_update().get(pk=pk)
    authorize(user, 'approve_journalentry', makers=(je.created_by_id,), reference=je.number)
    if je.kind != 'manual' or je.status != 'draft':
        raise ValidationError('Only draft journal vouchers can be decided.')
    if je.created_by_id == user.pk:
        raise ValidationError('The person who drafted the voucher cannot approve it (maker-checker).')
    if approve:
        check_open(je.date)
    je.status, je.approved_by = ('posted' if approve else 'rejected'), user
    je.save()
    AuditEvent.objects.create(actor=user, site=je.site, action='Journal voucher approved' if approve else 'Journal voucher rejected', reference=je.number, detail=note[:200])
    return je


# ---------- Reports ----------

def _posted(start=None, end=None, site=None):
    qs = JournalLine.objects.filter(entry__status='posted')
    if start: qs = qs.filter(entry__date__gte=start)
    if end: qs = qs.filter(entry__date__lte=end)
    if site: qs = qs.filter(entry__site=site)
    return qs


def trial_balance(start, end, site=None):
    opening = {r['account']: r['d'] - r['c'] for r in _posted(None, start and date.fromordinal(start.toordinal() - 1), site).values('account').annotate(d=Sum('debit'), c=Sum('credit'))} if start else {}
    period = {r['account']: (r['d'], r['c']) for r in _posted(start, end, site).values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    rows = []
    for a in Account.objects.filter(pk__in=set(opening) | set(period)).order_by('code'):
        o = opening.get(a.pk, ZERO)
        d, c = period.get(a.pk, (ZERO, ZERO))
        rows.append({'account': a, 'opening': o, 'debit': d, 'credit': c, 'closing': o + d - c})
    d, c = sum((r['debit'] for r in rows), ZERO), sum((r['credit'] for r in rows), ZERO)
    return {'rows': rows, 'debit': d, 'credit': c, 'diff': d - c,
            'closing_dr': sum((r['closing'] for r in rows if r['closing'] > 0), ZERO), 'closing_cr': -sum((r['closing'] for r in rows if r['closing'] < 0), ZERO)}


def _grouped(rows, sign):
    groups = defaultdict(list)
    for a, amt in rows:
        if amt:
            groups[a.group].append((a, amt * sign))
    order = {g: i for i, g in enumerate(GROUP_ORDER)}
    return [(g, items, sum((x[1] for x in items), ZERO)) for g, items in sorted(groups.items(), key=lambda kv: order.get(kv[0], 99))]


def profit_and_loss(start, end, site=None):
    sums = {r['account']: r['d'] - r['c'] for r in _posted(start, end, site).exclude(entry__kind='yearend').filter(account__type__in=['income', 'expense']).values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    accts = {a.pk: a for a in Account.objects.filter(pk__in=sums)}
    income = _grouped([(accts[k], v) for k, v in sums.items() if accts[k].type == 'income'], -1)
    expense = _grouped([(accts[k], v) for k, v in sums.items() if accts[k].type == 'expense'], 1)
    ti, te = sum((g[2] for g in income), ZERO), sum((g[2] for g in expense), ZERO)
    return {'income': income, 'expense': expense, 'total_income': ti, 'total_expense': te, 'profit': ti - te}


def balance_sheet(as_of, site=None):
    sums = {r['account']: r['d'] - r['c'] for r in _posted(None, as_of, site).values('account').annotate(d=Sum('debit'), c=Sum('credit'))}
    accts = {a.pk: a for a in Account.objects.filter(pk__in=sums)}
    assets = _grouped([(accts[k], v) for k, v in sums.items() if accts[k].type == 'asset'], 1)
    liab = _grouped([(accts[k], v) for k, v in sums.items() if accts[k].type == 'liability'], -1)
    equity = _grouped([(accts[k], v) for k, v in sums.items() if accts[k].type == 'equity'], -1)
    profit = -sum((v for k, v in sums.items() if accts[k].type in ('income', 'expense')), ZERO)
    ta = sum((g[2] for g in assets), ZERO)
    tl = sum((g[2] for g in liab), ZERO) + sum((g[2] for g in equity), ZERO) + profit
    return {'assets': assets, 'liabilities': liab, 'equity': equity, 'profit': profit, 'total_assets': ta, 'total_liabilities': tl, 'difference': ta - tl}


def account_ledger(account, start, end, partner=None, site=None):
    base = JournalLine.objects.filter(entry__status='posted', account=account)
    if partner: base = base.filter(partner=partner)
    if site: base = base.filter(entry__site=site)
    o = base.filter(entry__date__lt=start).aggregate(d=Sum('debit'), c=Sum('credit')) if start else {'d': 0, 'c': 0}
    running = (o['d'] or ZERO) - (o['c'] or ZERO)
    opening = running
    rows = []
    for l in base.filter(entry__date__gte=start, entry__date__lte=end).select_related('entry', 'partner').order_by('entry__date', 'entry__pk', 'pk'):
        running += l.debit - l.credit
        rows.append({'line': l, 'balance': running})
    return {'opening': opening, 'rows': rows, 'closing': running}


def subledger_check(as_of=None):
    if as_of is not None:
        from .closing import historical_checks
        return historical_checks(as_of)
    """Control accounts vs their subledgers (FR-FIN-003)."""
    from .models import Partner, StockBalance
    from . import ledger
    out = []
    ar = _posted().filter(account__system_key='ar').aggregate(d=Sum('debit'), c=Sum('credit'))
    sub_ar = sum((ledger.balance(p) for p in Partner.objects.filter(Q(invoices__isnull=False) | Q(receipts__isnull=False)).distinct()), ZERO)
    out.append(('Trade receivables', (ar['d'] or ZERO) - (ar['c'] or ZERO), sub_ar))
    inv = _posted().filter(account__system_key__in=['inventory_rm', 'inventory_stores', 'inventory_fg']).aggregate(d=Sum('debit'), c=Sum('credit'))
    sub_inv = sum((b.value for b in StockBalance.objects.all()), ZERO)
    out.append(('Inventory', (inv['d'] or ZERO) - (inv['c'] or ZERO), sub_inv))
    from . import payables
    from .models import FreightBill, FreightRecoveryAdjustment, PaymentAllocation
    ap = _posted().filter(account__system_key='ap').aggregate(d=Sum('debit'), c=Sum('credit'))
    sub_ap = sum((payables.balance(p)['balance'] for p in Partner.objects.filter(Q(kind='Supplier') | Q(supplier_invoices__isnull=False) | Q(assets__credit_purchase=True) | Q(debit_notes__isnull=False)).distinct()), ZERO)
    out.append(('Supplier payables', (ap['c'] or ZERO) - (ap['d'] or ZERO), sub_ap))
    freight = _posted().filter(account__system_key='ap_freight').aggregate(d=Sum('debit'), c=Sum('credit'))
    freight_billed = FreightBill.objects.filter(status='approved').aggregate(t=Sum('payable'))['t'] or ZERO
    freight_billed -= FreightRecoveryAdjustment.objects.filter(status='approved').aggregate(t=Sum('amount'))['t'] or ZERO
    freight_paid = PaymentAllocation.objects.filter(freight_bill__isnull=False, payment__status='paid').aggregate(t=Sum('amount'))['t'] or ZERO
    out.append(('Transporter payables', (freight['c'] or ZERO) - (freight['d'] or ZERO), freight_billed - freight_paid))
    from .deposits import held
    dep = _posted().filter(account__system_key='security_deposits').aggregate(d=Sum('debit'), c=Sum('credit'))
    if dep['d'] or dep['c']:
        out.append(('Dealer security deposits', (dep['c'] or ZERO) - (dep['d'] or ZERO), held()))
    return [{'name': n, 'gl': g, 'sub': s, 'diff': g - s} for n, g, s in out]
