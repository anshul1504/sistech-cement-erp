"""Excel report packs (SRS §11: scheduled reports; §11.1: exports keep filters, generation time and scope).

A pack is built for one user with that user's dashboard permissions and plant access, from the same KPI engine as the
dashboards (erp.insights), so the numbers in the file match the screens. Each sheet starts with title, period, plants,
generation time and the 'posted documents only' note.
"""
import io
from datetime import timedelta

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from . import insights as I

PACK_AREA = {'management': 'executive', 'sales': 'sales', 'credit': 'credit', 'stock': 'stock'}
NAVY, GOLD = '082859', 'F4BD18'


def allowed(user, pack):
    from .insight_views import AREA_MAP
    area = PACK_AREA.get(pack)
    return bool(area) and user.is_active and AREA_MAP[area][3](user)


def period_for(frequency, today=None):
    today = today or timezone.localdate()
    if frequency == 'daily':
        d = today - timedelta(days=1)
        return I.Period('custom', d, d, d.strftime('%d %b %Y'))
    if frequency == 'weekly':
        return I.Period('custom', today - timedelta(days=7), today - timedelta(days=1), 'Last 7 days')
    end = today.replace(day=1) - timedelta(days=1)
    return I.Period('last_month', end.replace(day=1), end, end.strftime('%B %Y'))


class _Book:
    def __init__(self, title, period, sites):
        self.wb, self.first, self.sections = Workbook(), True, []
        self.title, self.period, self.scope = title, period, ', '.join(s.code for s in sites) or 'none'

    def sheet(self, name, headers, rows, widths=None):
        self.sections.append((name, headers, rows))
        ws = self.wb.active if self.first else self.wb.create_sheet()
        self.first = False
        ws.title = name[:31]
        ws.append([f'SISTECH ERP · {self.title} · {name}'])
        ws.append([f'Period {self.period.start:%d-%m-%Y} to {self.period.end:%d-%m-%Y} · Plants: {self.scope} · Generated {timezone.localtime():%d-%m-%Y %H:%M} IST · posted documents only'])
        ws['A1'].font = Font(bold=True, size=13, color=NAVY)
        ws['A2'].font = Font(italic=True, size=9, color='6B778A')
        ws.append([])
        ws.append(headers)
        for c in ws[4]:
            c.font, c.fill, c.alignment = Font(bold=True, color='FFFFFF'), PatternFill('solid', fgColor=NAVY), Alignment(wrap_text=True, vertical='center')
        for r in rows:
            ws.append([float(v) if hasattr(v, 'quantize') else v for v in r])
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, max(12, min(40, len(str(h)) + 4)))
        ws.freeze_panes = 'A5'
        for row in ws.iter_rows(min_row=5):
            for c in row:
                if isinstance(c.value, float):
                    c.number_format = '#,##,##0.00'
        return ws

    def bytes(self):
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()


def _sales_sheets(book, s):
    book.sheet('Sales summary', ['Measure', 'Value'], [
        ['Net sales (₹, excl. GST)', s['value']], ['Net volume (MT)', s['mt']], ['Earlier period sales (₹)', s['prev_value']],
        ['Change %', s['value_change']], ['Realisation (₹/MT)', s['per_mt']], ['Invoices', s['invoices']], ['Buying customers', s['buyers']],
        ['Target (₹)', s['target']], ['Target achievement %', s['target_pct']]], {1: 34, 2: 18})
    for key, name in (('products', 'By product'), ('states', 'By state'), ('districts', 'By district'), ('dealers', 'Top customers'), ('owners', 'By sales person')):
        book.sheet(name, [name.replace('By ', '').replace('Top ', '').capitalize(), 'Net MT', 'Net sales ₹', 'Share %', '₹ per MT'],
                   [[r['label'], r['mt'], r['value'], r['share'], r['per_mt']] for r in s[key]], {1: 34})


def _credit_sheets(book, c):
    book.sheet('Collections', ['Measure', 'Value'], [
        ['Collected (₹)', c['collected']], ['Receipts', c['receipts']], ['Billed incl. GST (₹)', c['billed']], ['Collection efficiency %', c['efficiency']],
        ['Total receivable (₹)', c['total']], ['Overdue (₹)', c['overdue']], ['90+ days (₹)', c['d90p']], ['DSO (days)', c['dso']],
        ['Advances held (₹)', c['advances']], ['Cheques bounced (₹)', c['bounced']]], {1: 34, 2: 18})
    book.sheet('Ageing', ['Bucket', 'Amount ₹', 'Share %'], [[b['label'], b['amount'], b['share']] for b in c['ageing']], {1: 20})
    book.sheet('Overdue customers', ['Customer', 'Overdue ₹', 'Outstanding ₹', 'Oldest (days)'],
               [[r['partner'].name, r['overdue'], r['due'], r['days']] for r in c['overdue_dealers']], {1: 36})
    book.sheet('Over credit limit', ['Customer', 'Balance ₹', 'Limit ₹', 'Excess ₹'],
               [[r['partner'].name, r['balance'], r['limit'], r['excess']] for r in c['over_limit']], {1: 36})


def _stock_sheets(book, st, d):
    book.sheet('Stock position', ['Plant / depot', 'Product', 'Unit', 'Available', 'Reserved', 'Free', 'Quality hold', 'In transit', 'Value ₹', 'Days of cover'],
               [[r['site'].code, r['product'].name, r['product'].unit, r['available'], r['reserved'], r['free'], r['hold'], r['transit'], r['value'], r['cover']]
                for r in st['rows']], {2: 32})
    book.sheet('Dispatch', ['Measure', 'Value'], [
        ['Dispatched (MT)', d['mt']], ['Trucks out', d['trips']], ['Avg turnaround (hours)', d['turnaround']], ['In transit / POD pending', d['transit']],
        ['POD pending > 48 h', d['pod_late']], ['Freight ₹ (Sistech paid)', d['freight']], ['Freight ₹ per MT', d['freight_per_mt']],
        ['Deliveries with shortage/damage', d['claims']], ['Weight exceptions open', d['exceptions']]], {1: 34, 2: 18})


def build(user, pack, period, fmt='xlsx'):
    """Returns (filename, bytes) or None when the user may not see this pack. fmt: 'xlsx' or 'pdf' (same numbers)."""
    if not allowed(user, pack):
        return None
    sites = list(I.visible_sites(user))
    fin = I.company_finance(user)
    title = dict((('management', 'Management summary'), ('sales', 'Sales'), ('credit', 'Credit & collections'), ('stock', 'Stock & dispatch')))[pack]
    book = _Book(title, period, sites)
    if pack in ('management', 'sales'):
        _sales_sheets(book, I.sales_summary(sites, period))
    if pack == 'management':
        b = I.order_book(sites)
        q = I.quality_summary(sites, period)
        sup = I.sourcing_summary(sites, period)
        book.sheet('Operations', ['Measure', 'Value'], [
            ['Order book (MT)', b['mt']], ['Order book (₹)', b['value']], ['Orders awaiting approval', b['waiting']],
            ['Partner supply received (MT)', sup['usable']], ['Partner commitment (MT)', sup['commit']], ['Partner fulfilment %', sup['fulfil']],
            ['Complaints open', q['complaints_open']], ['Complaints past SLA', q['complaints_late']], ['CAPA overdue', q['capa_late']]], {1: 34, 2: 18})
        if fin and user.has_perm('erp.view_financials'):
            f = I.finance_summary(period)
            book.sheet('Finance', ['Measure', 'Value'], [
                ['Revenue (₹)', f['revenue']], ['Gross margin (₹)', f['gross']], ['Gross margin %', f['gross_pct']], ['Net profit (₹)', f['profit']],
                ['Cash & bank (₹)', f['cash']], ['Receivable (₹)', f['ar']], ['Payable (₹)', f['ap']], ['GST net payable (₹)', f['gst_net']]], {1: 34, 2: 18})
    if pack == 'credit' or (pack == 'management' and fin):
        _credit_sheets(book, I.receivables(period))
    if pack in ('stock', 'management') and user.has_perm('erp.view_stockbalance'):
        _stock_sheets(book, I.stock_summary(sites), I.dispatch_summary(sites, period))
    name = f'sistech-{pack}-{period.start:%Y%m%d}-{period.end:%Y%m%d}'
    if fmt == 'pdf':
        return name + '.pdf', _pdf(book, title, period)
    return name + '.xlsx', book.bytes()


def _cell(v):
    from .pdf import money
    if v is None:
        return ''
    if hasattr(v, 'quantize') or isinstance(v, float):
        return money(v)
    return str(v)


def _pdf(book, title, period):
    from .pdf import render
    sections = []
    for name, headers, rows in book.sections:
        numeric = {i for i in range(1, len(headers))}
        sections.append({'title': name, 'headers': headers, 'rows': [[_cell(v) for v in r] for r in rows], 'numeric': numeric,
                         'widths': [0.7, 0.3] if len(headers) == 2 else [0.34] + [0.66 / (len(headers) - 1)] * (len(headers) - 1)})
    return render({'title': title, 'number': period.label, 'date': f'{period.start:%d-%m-%Y} to {period.end:%d-%m-%Y}', 'landscape': any(len(h) > 6 for _, h, _ in book.sections),
                   'meta': [('Plants', book.scope), ('Basis', 'Posted documents only')], 'sections': sections})


def due(sub, today=None):
    today = today or timezone.localdate()
    if sub.last_sent_at and timezone.localtime(sub.last_sent_at).date() == today:
        return False
    return sub.frequency == 'daily' or (sub.frequency == 'weekly' and today.weekday() == 0) or (sub.frequency == 'monthly' and today.day == 1)
