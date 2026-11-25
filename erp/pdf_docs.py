"""PDF builders for documents and reports. Each builder enforces the same permission and plant scope as its screen and
returns (spec, filename) for erp.pdf.render. Every download is recorded in the audit trail by the view."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .gst import in_words, summarise
from .models import INDIAN_STATES
from .pdf import money
from .services import scope, permitted

ZERO = Decimal('0')
STATES = dict(INDIAN_STATES)
q = lambda v: f'{Decimal(v).normalize():f}' if v not in (None, '') else ''
d = lambda v: f'{v:%d-%m-%Y}' if v else ''
R = lambda v, dp=2: f'₹{money(v, dp)}'


def _finance(user, perm):
    from .finance_access import permitted as fp
    fp(user, perm)


def _org():
    from .models import Organization
    return Organization.objects.defer('logo', 'icon').filter(pk=1).first() or Organization()


def _tax_rows(tax, interstate):
    rows = [('Taxable value', R(tax['taxable']), False)]
    rows += [('IGST', R(tax['igst']), False)] if interstate else [('CGST', R(tax['cgst']), False), ('SGST', R(tax['sgst']), False)]
    if tax.get('round_off'):
        rows.append(('Round off', R(tax['round_off']), False))
    return rows


def _line_section(rows, unit_attr=True):
    out = []
    for i, r in enumerate(rows, 1):
        l = r['line']
        name = getattr(l, 'description', '') or l.product.name
        out.append([i, name, l.hsn or '', f'{q(l.quantity)} {getattr(l, "unit", "") or l.product.unit}', money(l.rate), money(r['taxable']), f'{q(l.gst_rate)}%', money(r['total'])])
    return {'headers': ['#', 'Description', 'HSN', 'Qty', 'Rate ₹', 'Taxable ₹', 'GST', 'Amount ₹'], 'widths': [.04, .32, .08, .12, .1, .13, .07, .14],
            'numeric': {3, 4, 5, 6, 7}, 'rows': out}


# ---------- Sales documents ----------

def invoice(user, pk, copy='Original for recipient'):
    from .models import Invoice
    permitted(user, 'view_invoice')
    inv = get_object_or_404(scope(Invoice.objects.select_related('site', 'partner', 'order'), user), pk=pk)
    lines = list(inv.lines.select_related('product'))
    rows = [{'line': l, 'taxable': l.taxable, 'total': l.total} for l in lines]
    tax = {'taxable': inv.taxable, 'cgst': inv.cgst, 'sgst': inv.sgst, 'igst': inv.igst, 'round_off': inv.round_off}
    org = _org()
    spec = {'title': 'Tax invoice', 'number': inv.number, 'date': d(inv.date), 'status': copy,
            'meta': [('Place of supply', f'{STATES.get(inv.place_of_supply, "")} ({inv.place_of_supply})'), ('Seller GSTIN', inv.seller_gstin), ('Due date', d(inv.due_date)),
                     ('Order', inv.order.number), ('Vehicle', inv.vehicle_number), ('Transporter', inv.transporter), ('LR / GR', inv.lr_number), ('E-way bill', inv.eway_bill),
                     ('Reverse charge', 'Yes' if inv.reverse_charge else 'No')],
            'parties': [('Bill to', [inv.buyer_name, inv.buyer_address, f'GSTIN {inv.buyer_gstin}' if inv.buyer_gstin else 'Unregistered', STATES.get(inv.buyer_state, '')]),
                        ('Ship to', [inv.ship_to or inv.buyer_address]), ('Dispatched from', [inv.seller_name, inv.seller_address])],
            'sections': [_line_section(rows)], 'totals': _tax_rows(tax, inv.interstate) + [('Invoice total', R(inv.grand_total, 0), True)],
            'words': in_words(inv.grand_total), 'bank': True, 'terms': org.terms_conditions, 'signatures': ["Receiver's signature", 'Authorised signatory']}
    return spec, f'{inv.number.replace("/", "-")}.pdf'


def credit_note(user, pk):
    from .models import CreditNote
    permitted(user, 'view_creditnote')
    cn = get_object_or_404(scope(CreditNote.objects.select_related('invoice', 'partner'), user, 'invoice__site'), pk=pk)
    rows = [[i, l.invoice_line.description, q(l.quantity) or '—', money(l.taxable), f'{q(l.gst_rate)}%', money(l.cgst + l.sgst + l.igst), money(l.taxable + l.cgst + l.sgst + l.igst)]
            for i, l in enumerate(cn.lines.select_related('invoice_line'), 1)]
    spec = {'title': 'Credit note', 'number': cn.number, 'date': d(cn.date),
            'meta': [('Against invoice', cn.invoice.number), ('Invoice date', d(cn.invoice.date)), ('Reason', cn.get_reason_display()), ('Stock returned', 'Yes' if cn.returns_stock else 'No')],
            'parties': [('Customer', [cn.invoice.buyer_name, cn.invoice.buyer_address, f'GSTIN {cn.invoice.buyer_gstin}' if cn.invoice.buyer_gstin else 'Unregistered'])],
            'sections': [{'headers': ['#', 'Item', 'Returned qty', 'Taxable ₹', 'GST', 'Tax ₹', 'Total ₹'], 'widths': [.05, .35, .12, .14, .08, .12, .14], 'numeric': {2, 3, 4, 5, 6}, 'rows': rows}],
            'totals': _tax_rows({'taxable': cn.taxable, 'cgst': cn.cgst, 'sgst': cn.sgst, 'igst': cn.igst, 'round_off': cn.round_off}, cn.invoice.interstate) + [('Credit total', R(cn.total, 0), True)],
            'words': in_words(cn.total), 'notes': [cn.notes], 'signatures': ['Authorised signatory']}
    return spec, f'{cn.number.replace("/", "-")}.pdf'


def order(user, pk):
    from .models import Order
    from accounts.roles import order_permission
    o = get_object_or_404(scope(Order.objects.select_related('partner', 'site', 'created_by', 'approved_by'), user), pk=pk)
    if not order_permission(user, o.kind, 'view'):
        raise PermissionDenied
    lines = list(o.lines.select_related('product'))
    tax = summarise(lines, o.is_interstate)
    sale = o.kind == 'Sale'
    meta = [('Plant / depot', f'{o.site.code} · {o.site.name}'), ('Status', o.status), ('Required by' if not sale else 'Requested date', d(o.due_date)),
            ('Reference', o.customer_ref), ('Place of supply', o.supply_state_name)]
    if sale:
        meta += [('Freight', o.get_freight_terms_display()), ('Priority', o.get_priority_display()), ('Unloading by', o.get_unloading_by_display())]
    else:
        meta += [('Credit days', o.payment_terms_days if o.payment_terms_days is not None else o.partner.payment_terms_days), ('Receipt tolerance', f'{q(o.receipt_tolerance_pct)}%'),
                 ('Advance', R(o.advance_amount) if o.advance_amount else ''), ('Delivery terms', o.delivery_terms)]
    p = o.partner
    spec = {'title': 'Sales order' if sale else 'Purchase order', 'number': o.number + (f' (rev {o.revision})' if o.revision > 1 else ''), 'date': d(timezone.localtime(o.created_at).date()),
            'status': o.status, 'meta': meta,
            'parties': [('Customer' if sale else 'Supplier', [p.name, p.full_address, f'GSTIN {p.gstin}' if p.gstin else '', p.phone]),
                        ('Ship to' if sale else 'Deliver to', [o.ship_to or (p.shipping_address or p.full_address) if sale else o.site.name])],
            'sections': [_line_section(tax['rows'])], 'totals': _tax_rows(tax, tax['interstate']) + [('Order total', R(tax['grand_total'], 0), True)],
            'words': in_words(tax['grand_total']), 'notes': [o.delivery_instructions if sale else '', o.notes],
            'terms': _org().terms_conditions if sale else 'Supply as per the agreed specification. Quantities are accepted after weighment and quality check at our plant; invoice must quote this PO number.',
            'signatures': ['Prepared by ' + (o.created_by.get_full_name() or o.created_by.username), 'Authorised signatory']}
    return spec, f'{o.number}.pdf'


def quotation(user, pk):
    from .models import Quotation
    from . import quotes as QT
    permitted(user, 'view_quotation')
    qt = get_object_or_404(scope(Quotation.objects.select_related('site', 'partner', 'lead'), user), pk=pk)
    t = QT.totals(qt)
    who = [qt.partner.name, qt.partner.full_address, f'GSTIN {qt.partner.gstin}' if qt.partner.gstin else ''] if qt.partner_id else [qt.lead.business_name, qt.lead.contact_name, qt.lead.district]
    spec = {'title': 'Quotation', 'number': f'{qt.number} · rev {qt.revision}', 'date': d(qt.date), 'status': qt.get_status_display(),
            'meta': [('Valid until', d(qt.valid_until)), ('Supplying from', qt.site.name), ('Freight', qt.get_freight_terms_display()), ('Place of supply', STATES.get(qt.place_of_supply, '')),
                     ('Payment terms', qt.payment_terms), ('Delivery', qt.delivery_terms)],
            'parties': [('Quotation for', who)], 'sections': [_line_section(t['rows'])],
            'totals': _tax_rows(t, t['interstate']) + [('Total incl. GST', R(t['grand_total'], 0), True)], 'words': in_words(t['grand_total']),
            'notes': [qt.notes, 'Prices are valid until the date above and subject to stock availability.'], 'terms': _org().terms_conditions, 'signatures': ['Authorised signatory']}
    return spec, f'quotation-{qt.number}-r{qt.revision}.pdf'


def receipt(user, pk):
    from .models import Receipt
    _finance(user, 'view_receipt')
    r = get_object_or_404(Receipt.objects.select_related('partner'), pk=pk)
    rows = [[s.invoice.number, d(s.invoice.date), money(s.invoice.grand_total), money(s.amount)] for s in r.settlements.select_related('invoice')]
    spec = {'title': 'Payment receipt', 'number': r.number, 'date': d(r.date), 'status': r.get_status_display(),
            'meta': [('Mode', r.get_mode_display()), ('UTR / cheque', r.reference), ('Cheque date', d(r.instrument_date)), ('Drawn on', r.drawn_on), ('Deposited to', r.deposited_to)],
            'parties': [('Received from', [r.partner.name, r.partner.full_address, f'GSTIN {r.partner.gstin}' if r.partner.gstin else ''])],
            'sections': [{'title': 'Applied to invoices', 'headers': ['Invoice', 'Date', 'Invoice total ₹', 'Applied ₹'], 'widths': [.3, .2, .25, .25], 'numeric': {2, 3}, 'rows': rows,
                          'empty': 'Kept as advance — will be applied to the next invoices.'}],
            'totals': [('Applied', R(r.allocated), False), ('Unapplied (advance)', R(r.unallocated), False), ('Amount received', R(r.amount), True)],
            'words': in_words(r.amount), 'notes': [r.notes], 'signatures': ['Authorised signatory']}
    return spec, f'{r.number.replace("/", "-")}.pdf'


def statement(user, pk):
    from .models import Partner
    from . import ledger
    _finance(user, 'view_receipt')
    p = get_object_or_404(Partner, pk=pk)
    today = timezone.localdate()
    start = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
    opening, rows = ledger.ledger_rows(p, start, today)
    return statement_spec(p, start, today, opening, rows), f'statement-{p.code}-{today}.pdf'


def statement_spec(p, start, end, opening, rows):
    """Render already-authorized ledger data; callers enforce account ownership."""
    from . import ledger
    data = [[d(r['date']), r['kind'], r['ref'], r['note'], money(r['debit']) if r['debit'] else '', money(r['credit']) if r['credit'] else '', money(r['balance'])] for r in rows]
    closing = rows[-1]['balance'] if rows else opening
    age = ledger.ageing(ledger.open_invoices(p))
    spec = {'title': 'Statement of account', 'number': p.code, 'date': f"{d(start) or 'Beginning'} to {d(end) or 'Latest'}",
            'meta': [('Opening balance', R(opening)), ('Closing balance', R(closing)), ('Credit limit', R(p.credit_limit, 0)), ('Payment terms', f'{p.payment_terms_days} days'),
                     ('Not due', R(age['current'])), ('Overdue', R(age['overdue'])), ('90+ days', R(age['d90p']))],
            'parties': [('Customer', [p.name, p.full_address, f'GSTIN {p.gstin}' if p.gstin else ''])],
            'sections': [{'headers': ['Date', 'Type', 'Document', 'Details', 'Debit ₹', 'Credit ₹', 'Balance ₹'], 'widths': [.1, .12, .15, .25, .12, .12, .14], 'numeric': {4, 5, 6},
                          'rows': [['', 'Opening', '', '', '', '', money(opening)]] + data, 'total_row': ['', 'Closing', '', '', '', '', money(closing)]}],
            'notes': ['Positive balance = amount due to us. Please report any difference within 15 days.'], 'bank': True}
    return spec


def return_request(user, pk):
    from .models import ReturnRequest
    permitted(user, 'view_returnrequest')
    r = get_object_or_404(scope(ReturnRequest.objects.select_related('partner', 'invoice', 'site', 'credit_note'), user), pk=pk)
    rows = [[l.invoice_line.description, q(l.quantity), q(l.received), q(l.good), q(l.damaged), l.note] for l in r.lines.select_related('invoice_line')]
    spec = {'title': 'Customer return (RMA)', 'number': r.number, 'date': d(timezone.localtime(r.created_at).date()), 'status': r.get_status_display(),
            'meta': [('Invoice', r.invoice.number), ('Reason', r.get_reason_display()), ('Plant', r.site.code), ('Pickup', d(r.pickup_on)), ('Vehicle', r.vehicle_number), ('LR', r.lr_number),
                     ('Credit note', r.credit_note.number if r.credit_note_id else '')],
            'parties': [('Customer', [r.partner.name, r.partner.full_address])],
            'sections': [{'headers': ['Item', 'Approved', 'Received', 'Good', 'Damaged', 'Note'], 'widths': [.3, .1, .1, .1, .1, .3], 'numeric': {1, 2, 3, 4}, 'rows': rows}],
            'notes': [r.description], 'signatures': ['Customer / driver', 'Store in-charge', 'Authorised signatory']}
    return spec, f'{r.number.replace("/", "-")}.pdf'


# ---------- Purchase & finance documents ----------

def grn(user, pk):
    from .models import GoodsReceipt
    permitted(user, 'view_goodsreceipt')
    g = get_object_or_404(scope(GoodsReceipt.objects.select_related('supplier', 'site', 'order'), user), pk=pk)
    rows = [[l.product.name, q(l.challan_qty), q(l.received), q(l.accepted), q(l.rejected), q(l.damaged), q(l.hold)] for l in g.lines.select_related('product')]
    spec = {'title': 'Goods receipt note', 'number': g.number, 'date': d(timezone.localtime(g.posted_at).date() if g.posted_at else None), 'status': g.get_status_display(),
            'meta': [('Purchase order', g.order.number), ('Challan / invoice', g.challan_number), ('Challan date', d(g.challan_date)), ('Vehicle', g.vehicle_number),
                     ('Transporter', g.transporter_name), ('E-way bill', g.eway_bill), ('Net weight', f'{g.net_kg:,} kg' if g.net_kg else '')],
            'parties': [('Supplier', [g.supplier.name, g.supplier.full_address, f'GSTIN {g.supplier.gstin}' if g.supplier.gstin else '']), ('Received at', [g.site.name])],
            'sections': [{'headers': ['Item', 'Challan qty', 'Received', 'Accepted', 'Rejected', 'Damaged', 'Quality hold'], 'widths': [.28, .12, .12, .12, .12, .12, .12], 'numeric': {1, 2, 3, 4, 5, 6}, 'rows': rows}],
            'notes': [g.variance_reason and f'Variance: {g.variance_reason}', g.notes], 'signatures': ['Driver', 'Store in-charge', 'Quality']}
    return spec, f'{g.number.replace("/", "-")}.pdf'


def debit_note(user, pk):
    from .models import DebitNote
    permitted(user, 'view_debitnote')
    n = get_object_or_404(scope(DebitNote.objects.select_related('supplier', 'site', 'grn', 'supplier_invoice'), user), pk=pk)
    rows = [[l.product.name, q(l.quantity), money(l.rate), f'{q(l.gst_rate)}%', money(l.amount)] for l in n.lines.select_related('product')]
    spec = {'title': 'Debit note', 'number': n.number, 'date': d(n.date),
            'meta': [('Reason', n.get_reason_display()), ('GRN', n.grn.number if n.grn_id else ''), ('Supplier invoice', n.supplier_invoice.invoice_number if n.supplier_invoice_id else ''),
                     ('Return vehicle', n.vehicle_number)],
            'parties': [('Supplier', [n.supplier.name, n.supplier.full_address, f'GSTIN {n.supplier.gstin}' if n.supplier.gstin else ''])],
            'sections': [{'headers': ['Item', 'Qty', 'Rate ₹', 'GST', 'Taxable ₹'], 'widths': [.4, .12, .16, .12, .2], 'numeric': {1, 2, 3, 4}, 'rows': rows}],
            'totals': [('Taxable', R(n.taxable), False), ('GST', R(n.tax), False), ('Debit total', R(n.total), True)], 'words': in_words(n.total),
            'notes': [n.remarks], 'signatures': ['Authorised signatory']}
    return spec, f'{n.number.replace("/", "-")}.pdf'


def payment(user, pk):
    from .models import SupplierPayment
    _finance(user, 'view_supplierpayment')
    p = get_object_or_404(SupplierPayment.objects.select_related('payee', 'bank', 'created_by', 'approved_by', 'paid_by'), pk=pk)
    rows = []
    for a in p.allocations.select_related('supplier_invoice', 'freight_bill', 'debit_note', 'fixed_asset'):
        doc = a.supplier_invoice or a.freight_bill or a.debit_note or a.fixed_asset
        rows.append([str(doc), money(a.amount)])
    spec = {'title': 'Payment voucher', 'number': p.number, 'date': d(p.date), 'status': p.get_status_display(),
            'meta': [('Mode', p.get_mode_display()), ('From bank', str(p.bank)), ('UTR / cheque', p.reference), ('Beneficiary A/c', p.beneficiary_account), ('IFSC', p.beneficiary_ifsc),
                     ('Prepared by', p.created_by.get_full_name() or p.created_by.username), ('Approved by', (p.approved_by.get_full_name() or p.approved_by.username) if p.approved_by else '')],
            'parties': [('Paid to', [p.beneficiary_name or p.payee.name, p.payee.full_address])],
            'sections': [{'headers': ['Against', 'Amount ₹'], 'widths': [.75, .25], 'numeric': {1}, 'rows': rows}],
            'totals': [('Paid', R(p.amount), True)], 'words': in_words(p.amount), 'notes': [p.note], 'signatures': ['Prepared by', 'Approved by', 'Receiver']}
    return spec, f'{p.number.replace("/", "-")}.pdf'


def journal(user, pk):
    from .models import JournalEntry
    _finance(user, 'view_journalentry')
    je = get_object_or_404(JournalEntry.objects.select_related('site', 'created_by', 'approved_by'), pk=pk)
    lines = list(je.lines.select_related('account', 'partner', 'cost_centre'))
    rows = [[f'{l.account.code} · {l.account.name}', l.partner.name if l.partner_id else '', l.cost_centre.code if l.cost_centre_id else '', l.narration,
             money(l.debit) if l.debit else '', money(l.credit) if l.credit else ''] for l in lines]
    dr = sum((l.debit for l in lines), ZERO)
    spec = {'title': 'Journal voucher', 'number': je.number, 'date': d(je.date), 'status': je.get_status_display(),
            'meta': [('Type', je.get_kind_display()), ('Plant / branch', je.site.code if je.site_id else 'Head office'), ('Source', je.source or ''),
                     ('Prepared by', je.created_by.get_full_name() or je.created_by.username), ('Approved by', (je.approved_by.get_full_name() or je.approved_by.username) if je.approved_by else '')],
            'sections': [{'headers': ['Account', 'Party', 'Cost centre', 'Note', 'Debit ₹', 'Credit ₹'], 'widths': [.28, .17, .1, .17, .14, .14], 'numeric': {4, 5}, 'rows': rows,
                          'total_row': ['Total', '', '', '', money(dr), money(dr)]}],
            'notes': [f'Narration: {je.narration}'], 'words': in_words(dr), 'signatures': ['Prepared by', 'Checked by', 'Approved by']}
    return spec, f'{je.number.replace("/", "-")}.pdf'


def commission_run(user, pk):
    from .models import CommissionRun
    _finance(user, 'view_commissionrun')
    r = get_object_or_404(CommissionRun.objects.select_related('plan'), pk=pk)
    rows = [[l.person.get_full_name() or l.person.username, q(l.basis_amount), money(l.cumulative), money(l.prior), money(l.amount), money(l.tds)] for l in r.lines.select_related('person')]
    spec = {'title': 'Commission statement', 'number': r.number, 'date': f'{d(r.start)} to {d(r.end)}', 'status': r.get_status_display(),
            'meta': [('Plan', r.plan.name), ('Basis', r.plan.get_basis_display()), ('Rate', f'₹{q(r.plan.rate)}/MT' if r.plan.basis == 'mt' else f'{q(r.plan.rate)}%'), ('TDS', f'{q(r.plan.tds_rate)}%')],
            'sections': [{'headers': ['Person', 'Basis', 'Cumulative ₹', 'Already given ₹', 'This run ₹', 'TDS ₹'], 'widths': [.26, .14, .15, .15, .15, .15], 'numeric': {1, 2, 3, 4, 5}, 'rows': rows,
                          'total_row': ['Total', '', '', '', money(r.total), money(r.tds)]}],
            'signatures': ['Calculated by', 'Approved by']}
    return spec, f'{r.number.replace("/", "-")}.pdf'


# ---------- Financial reports ----------

def _range(params):
    today = timezone.localdate()
    try:
        start = date.fromisoformat(params.get('from')) if params.get('from') else date(today.year if today.month >= 4 else today.year - 1, 4, 1)
        end = date.fromisoformat(params.get('to')) if params.get('to') else today
    except ValueError:
        start, end = date(today.year if today.month >= 4 else today.year - 1, 4, 1), today
    return start, end


def trial_balance(user, params):
    from . import gl
    _finance(user, 'view_financials')
    start, end = _range(params)
    tb = gl.trial_balance(start, end)
    rows = [[r['account'].code, r['account'].name, r['account'].group, money(r['opening']), money(r['debit']), money(r['credit']), money(r['closing'])] for r in tb['rows']]
    return ({'title': 'Trial balance', 'number': f'{d(start)} to {d(end)}', 'landscape': True,
             'sections': [{'headers': ['Code', 'Account', 'Group', 'Opening ₹', 'Debit ₹', 'Credit ₹', 'Closing ₹'], 'widths': [.07, .25, .2, .12, .12, .12, .12], 'numeric': {3, 4, 5, 6}, 'rows': rows,
                           'total_row': ['', 'Total', '', '', money(tb['debit']), money(tb['credit']), '']}],
             'notes': [f'Difference: {R(tb["diff"])}' if tb['diff'] else 'Debits equal credits.']}, f'trial-balance-{end}.pdf')


def profit_loss(user, params):
    from . import gl
    _finance(user, 'view_financials')
    start, end = _range(params)
    pl = gl.profit_and_loss(start, end)
    rows = []
    for title, groups in (('INCOME', pl['income']), ('EXPENSES', pl['expense'])):
        rows.append([title, '', ''])
        for g, items, total in groups:
            for a, amt in items:
                rows.append([f'   {a.code} · {a.name}', g, money(amt)])
            rows.append([f'Total {g}', '', money(total)])
    return ({'title': 'Profit & loss', 'number': f'{d(start)} to {d(end)}',
             'sections': [{'headers': ['Account', 'Group', 'Amount ₹'], 'widths': [.5, .3, .2], 'numeric': {2}, 'rows': rows}],
             'totals': [('Total income', R(pl['total_income']), False), ('Total expenses', R(pl['total_expense']), False), ('Net profit' if pl['profit'] >= 0 else 'Net loss', R(pl['profit']), True)]},
            f'profit-loss-{start}-{end}.pdf')


def balance_sheet(user, params):
    from . import gl
    _finance(user, 'view_financials')
    _, end = _range(params)
    bs = gl.balance_sheet(end)
    rows = []
    for title, groups in (('ASSETS', bs['assets']), ('LIABILITIES', bs['liabilities']), ('EQUITY', bs['equity'])):
        rows.append([title, ''])
        for g, items, total in groups:
            rows.append([f'   {g}', money(total)])
    rows.append(['   Profit / loss for the period', money(bs['profit'])])
    return ({'title': 'Balance sheet', 'number': f'as at {d(end)}', 'sections': [{'headers': ['Particulars', 'Amount ₹'], 'widths': [.7, .3], 'numeric': {1}, 'rows': rows}],
             'totals': [('Total assets', R(bs['total_assets']), True), ('Total liabilities & equity', R(bs['total_liabilities']), True)],
             'notes': [f'Difference: {R(bs["difference"])}' if bs['difference'] else '']}, f'balance-sheet-{end}.pdf')


def outstanding(user, params):
    from . import ledger
    from .models import Partner
    _finance(user, 'view_receipt')
    invoices = ledger.open_invoices()
    today = timezone.localdate()
    per = {}
    for i in invoices:
        r = per.setdefault(i.partner_id, {'p': i.partner, 'inv': []})
        r['inv'].append(i)
    rows = []
    for v in sorted(per.values(), key=lambda v: v['p'].name):
        a = ledger.ageing(v['inv'], today)
        rows.append([v['p'].name, v['p'].district, money(a['current']), money(a['d30']), money(a['d60']), money(a['d90']), money(a['d90p']), money(a['total'])])
    tot = ledger.ageing(invoices, today)
    return ({'title': 'Outstanding & ageing', 'number': f'as at {d(today)}', 'landscape': True,
             'sections': [{'headers': ['Customer', 'District', 'Not due ₹', '1–30 ₹', '31–60 ₹', '61–90 ₹', '90+ ₹', 'Total ₹'], 'widths': [.24, .12, .11, .1, .1, .1, .1, .13],
                           'numeric': {2, 3, 4, 5, 6, 7}, 'rows': rows,
                           'total_row': ['Total', '', money(tot['current']), money(tot['d30']), money(tot['d60']), money(tot['d90']), money(tot['d90p']), money(tot['total'])]}]},
            f'outstanding-{today}.pdf')


def budget_vs_actual(user, params):
    from . import budgets as B
    from .models import Budget
    _finance(user, 'view_budget')
    b = Budget.objects.filter(pk=params.get('budget') or 0).first() or Budget.objects.filter(status='approved').first() or Budget.objects.exclude(status='rejected').first()
    if not b:
        from django.http import Http404
        raise Http404('No budget yet.')
    upto = timezone.localdate()
    try:
        upto = date.fromisoformat(params['upto']) if params.get('upto') else upto
    except ValueError:
        pass
    data = B.comparison(b, upto)
    rows = []
    for g in data['groups']:
        rows.append([g['group'], '', '', '', ''])
        for r in g['rows']:
            rows.append([f'   {r["account"].code} · {r["account"].name}', money(r['budget']), money(r['actual']), money(r['variance']), f'{r["pct"]}%' if r['pct'] is not None else ''])
    s = data['summary']
    return ({'title': 'Budget vs actual', 'number': str(b), 'date': f'to {d(upto)}',
             'sections': [{'headers': ['Account', 'Budget to date ₹', 'Actual ₹', 'Variance ₹ (+ favourable)', 'Used'], 'widths': [.4, .15, .15, .18, .12], 'numeric': {1, 2, 3, 4}, 'rows': rows}],
             'totals': [('Budget profit to date', R(s['budget']['profit']), False), ('Actual profit to date', R(s['actual']['profit']), True)]}, f'budget-vs-actual-{upto}.pdf')


DOCS = {'invoice': invoice, 'credit-note': credit_note, 'order': order, 'quotation': quotation, 'receipt': receipt, 'statement': statement, 'return': return_request,
        'grn': grn, 'debit-note': debit_note, 'payment': payment, 'journal': journal, 'commission': commission_run}
REPORTS = {'trial-balance': trial_balance, 'profit-loss': profit_loss, 'balance-sheet': balance_sheet, 'outstanding': outstanding, 'budget-vs-actual': budget_vs_actual}


# ---------- Gate, weighbridge and quality documents ----------

def _weigh_rows(ws):
    return [[w.get_kind_display(), f'{w.weight_kg:,} kg', w.get_source_display(), w.scale_id, timezone.localtime(w.captured_at).strftime('%d-%m-%Y %H:%M'),
             (w.captured_by.get_full_name() or w.captured_by.username), 'Superseded' if w.superseded_by_id else 'Current', w.reason] for w in ws]


def _dispatch(user, pk):
    from .models import Dispatch
    permitted(user, 'view_dispatch')
    return get_object_or_404(scope(Dispatch.objects.select_related('order__partner', 'vehicle', 'driver', 'site', 'transporter'), user), pk=pk)


def weighment_slip(user, pk):
    dsp = _dispatch(user, pk)
    if not dsp.gross:
        raise PermissionDenied
    lines = [[l.order_line.product.name, f'{q(l.quantity)} {l.order_line.product.unit}', l.batch.batch if l.batch_id else ''] for l in dsp.lines.select_related('order_line__product', 'batch')]
    spec = {'title': 'Weighment slip', 'number': dsp.number, 'date': d(timezone.localtime(dsp.gross.captured_at).date()),
            'meta': [('Vehicle', dsp.vehicle.registration), ('Driver', dsp.driver.name), ('Token', dsp.token), ('Plant', dsp.site.code), ('Scale', dsp.site.scale_id),
                     ('Tare', f'{dsp.tare.weight_kg:,} kg' if dsp.tare else ''), ('Gross', f'{dsp.gross.weight_kg:,} kg'), ('Net', f'{dsp.net_kg:,} kg' if dsp.net_kg else ''),
                     ('Expected', f'{dsp.expected_kg:,} kg' if dsp.expected_kg else ''), ('Variance', f'{dsp.variance_kg} kg' if dsp.variance_kg is not None else '')],
            'parties': [('Customer', [dsp.order.partner.name, dsp.order.number]), ('Transporter', [dsp.transporter.name if dsp.transporter_id else 'Own / customer vehicle', dsp.lr_number and f'LR {dsp.lr_number}'])],
            'sections': [{'title': 'Readings', 'headers': ['Reading', 'Weight', 'Source', 'Scale', 'Time', 'Operator', 'Status', 'Reweigh reason'], 'widths': [.09, .11, .1, .1, .15, .15, .1, .2],
                          'numeric': {1}, 'rows': _weigh_rows(dsp.weighments.select_related('captured_by'))},
                         {'title': 'Load', 'headers': ['Product', 'Loaded', 'Batch'], 'widths': [.5, .25, .25], 'numeric': {1}, 'rows': lines}],
            'notes': [f'Weight exception: {dsp.exception}' if dsp.exception else ''], 'signatures': ['Driver', 'Weighbridge operator', 'Supervisor']}
    return spec, f'weighment-{dsp.number.replace("/", "-")}.pdf'


def gate_pass(user, pk):
    dsp = _dispatch(user, pk)
    inv = getattr(dsp, 'invoice', None)
    if inv is None:
        raise PermissionDenied
    lines = [[l.order_line.product.name, f'{q(l.quantity)} {l.order_line.product.unit}', l.batch.batch if l.batch_id else ''] for l in dsp.lines.select_related('order_line__product', 'batch')]
    spec = {'title': 'Gate pass', 'number': dsp.number, 'date': d(timezone.localdate()),
            'meta': [('Vehicle', dsp.vehicle.registration), ('Driver', f'{dsp.driver.name} {dsp.driver.mobile}'), ('Invoice', inv.number), ('E-way bill', inv.eway_bill),
                     ('Seal', dsp.seal_number), ('Net weight', f'{dsp.net_kg:,} kg' if dsp.net_kg else ''), ('LR / GR', dsp.lr_number), ('Plant', dsp.site.code)],
            'parties': [('Consignee', [inv.buyer_name, inv.ship_to or inv.buyer_address])],
            'sections': [{'headers': ['Product', 'Quantity', 'Batch'], 'widths': [.5, .25, .25], 'numeric': {1}, 'rows': lines}],
            'notes': ['Vehicle allowed to leave only with this pass, the tax invoice and the e-way bill.'], 'signatures': ['Driver', 'Dispatch in-charge', 'Security']}
    return spec, f'gatepass-{dsp.number.replace("/", "-")}.pdf'


def grn_slip(user, pk):
    return grn(user, pk)


def coa(user, pk):
    from .models import Production
    from . import quality as QC
    permitted(user, 'view_production')
    p = get_object_or_404(scope(Production.objects.select_related('site', 'product', 'released_by'), user), pk=pk)
    if p.status != 'Released':
        raise PermissionDenied
    org = _org()
    ev = QC.evidence(p)
    rows = [[r['spec'].parameter, r['spec'].method, r['spec'].unit, r['spec'].limit_text, q(r['result'].value) if r['result'] else '—',
             ('Conforms' if r['result'].ok else 'Deviation') if r['result'] else f'Result at {r["spec"].age_days} days'] for r in ev['rows']]
    lic = org.bis_cement_licence if p.product.category == 'Cement' else org.bis_tmt_licence if p.product.category == 'TMT' else ''
    spec = {'title': 'Mill test certificate' if p.product.category == 'TMT' else 'Test certificate', 'number': p.batch, 'date': d(p.released_at.date() if p.released_at else None),
            'meta': [('Product', p.product.name), ('Grade', p.product.grade or ''), ('Standard', p.product.standard or ''), ('Quantity', f'{q(p.actual_quantity)} {p.product.unit}'),
                     ('Produced', d(p.completed_at.date() if p.completed_at else None)), ('Plant', p.site.name), ('BIS licence', lic)],
            'sections': [{'headers': ['Parameter', 'Method', 'Unit', 'Requirement', 'Result', 'Remark'], 'widths': [.24, .16, .1, .18, .12, .2], 'numeric': {4}, 'rows': rows}],
            'notes': [f'Released under deviation: {p.disposition_note}' if p.disposition == 'deviation' else 'The batch conforms to the requirements tested above.'],
            'signatures': ['Chemist / analyst', 'Quality manager']}
    return spec, f'coa-{p.batch}.pdf'


DOCS.update({'weighment-slip': weighment_slip, 'gate-pass': gate_pass, 'grn-slip': grn_slip, 'coa': coa})
