"""One PDF design for every printable document and report (ReportLab, pure Python — no browser or system libraries).

A document is described as a dict and rendered by `render(spec)`:
    title, number, date, status            — top-right document box
    meta: [(label, value)]                 — key facts grid under the header
    parties: [(heading, [lines])]          — e.g. Bill to / Ship to / Supplier
    sections: [{'title', 'headers', 'rows', 'widths', 'numeric': {col indexes}, 'total_row'}]
    totals: [(label, value, bold)]         — right-aligned totals block
    words: 'Rupees … only'                 — amount in words
    notes: [text], terms: text, signatures: [label]
    landscape: bool
Header: company logo, legal name, address, GSTIN/PAN/CIN, contact. Footer: page x of y, generation time, footer note.
Fonts: the locally hosted Poppins TTF files (they include the ₹ sign).
"""
import io
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Table, TableStyle, Spacer, KeepTogether
from xml.sax.saxutils import escape

NAVY = colors.HexColor('#082859')
BLUE = colors.HexColor('#073777')
GOLD = colors.HexColor('#F4BD18')
INK = colors.HexColor('#24344A')
GREY = colors.HexColor('#6B778A')
LINE = colors.HexColor('#DCE2E9')
SOFT = colors.HexColor('#F3F5F8')

_FONTS = False


def _fonts():
    global _FONTS
    if _FONTS:
        return
    base = Path(settings.BASE_DIR) / 'static' / 'fonts'
    for name, file in (('Poppins', 'Poppins-Regular.ttf'), ('Poppins-Medium', 'Poppins-Medium.ttf'), ('Poppins-SemiBold', 'Poppins-SemiBold.ttf'), ('Poppins-Bold', 'Poppins-Bold.ttf')):
        pdfmetrics.registerFont(TTFont(name, str(base / file)))
    pdfmetrics.registerFontFamily('Poppins', normal='Poppins', bold='Poppins-SemiBold', italic='Poppins', boldItalic='Poppins-Bold')
    _FONTS = True


def _style(size=8.5, font='Poppins', color=INK, align=TA_LEFT, leading=None):
    return ParagraphStyle('s', fontName=font, fontSize=size, textColor=color, alignment=align, leading=leading or size * 1.35)


def P(text, size=8.5, font='Poppins', color=INK, align=TA_LEFT):
    return Paragraph(escape(str(text if text is not None else '')).replace('\n', '<br/>'), _style(size, font, color, align))


def money(v, decimals=2):
    """Indian grouping: 12,34,567.89."""
    from .templatetags.erp_format import indian
    if v in (None, ''):
        return ''
    return indian(v, decimals)


def _logo(org):
    data = None
    if org and org.logo and (org.logo_type or '').lower() in ('image/png', 'image/jpeg', 'image/jpg'):
        data = bytes(org.logo)
    if data is None:
        p = Path(settings.BASE_DIR) / 'static' / 'brand' / 'sistech-logo-email.png'
        if p.exists():
            data = p.read_bytes()
    return data


def _company():
    from .models import Organization, INDIAN_STATES
    org = Organization.objects.defer('icon').filter(pk=1).first() or Organization()
    addr = ', '.join(x for x in [org.address.strip(), org.city, f'{dict(INDIAN_STATES).get(org.state, "")} {org.pincode}'.strip()] if x)
    ids = ' · '.join(x for x in [f'GSTIN {org.gstin}' if org.gstin else '', f'PAN {org.pan}' if org.pan else '', f'CIN {org.cin}' if org.cin else ''] if x)
    contact = ' · '.join(x for x in [org.phone, org.email, org.website.replace('https://', '').replace('http://', '') if org.website else ''] if x)
    return org, addr, ids, contact


def render(spec):
    _fonts()
    org, addr, ids, contact = _company()
    size = landscape(A4) if spec.get('landscape') else A4
    width = size[0] - 24 * mm
    buf = io.BytesIO()
    generated = timezone.localtime()
    footer_note = spec.get('footer') or org.document_footer or 'This is a computer-generated document.'

    def decorate(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(GOLD)
        canvas.rect(0, size[1] - 4, size[0], 4, stroke=0, fill=1)
        canvas.setFont('Poppins', 7)
        canvas.setFillColor(GREY)
        canvas.drawString(12 * mm, 8 * mm, f'{footer_note}  ·  Generated {generated:%d-%m-%Y %H:%M} IST  ·  Sistech ERP by The Webfix')
        canvas.drawRightString(size[0] - 12 * mm, 8 * mm, f'Page {doc.page}')
        canvas.setStrokeColor(LINE)
        canvas.line(12 * mm, 11 * mm, size[0] - 12 * mm, 11 * mm)
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm, bottomMargin=16 * mm,
                            title=f"{spec.get('title', '')} {spec.get('number', '')}".strip(), author=org.legal_name, creator='Sistech ERP')
    story = []

    # Header: logo + company | document box
    logo = _logo(org)
    left = []
    if logo:
        from reportlab.platypus import Image
        iw, ih = ImageReader(io.BytesIO(logo)).getSize()
        h = 13 * mm
        left.append(Image(io.BytesIO(logo), width=min(h * iw / ih if ih else 40 * mm, 70 * mm), height=h, hAlign='LEFT'))
    left += [P(org.legal_name, 11, 'Poppins-Bold', NAVY)]
    if addr: left.append(P(addr, 7.5, color=GREY))
    if ids: left.append(P(ids, 7.5, color=GREY))
    if contact: left.append(P(contact, 7.5, color=GREY))
    box = [P(spec.get('title', '').upper(), 13, 'Poppins-Bold', BLUE, TA_RIGHT)]
    if spec.get('number'): box.append(P(spec['number'], 10, 'Poppins-SemiBold', INK, TA_RIGHT))
    if spec.get('date'): box.append(P(spec['date'], 8, color=GREY, align=TA_RIGHT))
    if spec.get('status'): box.append(P(spec['status'], 8, 'Poppins-SemiBold', colors.HexColor('#B54708'), TA_RIGHT))
    head = Table([[left, box]], colWidths=[width * 0.6, width * 0.4])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0), ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                              ('LINEBELOW', (0, 0), (-1, 0), 1.2, NAVY), ('BOTTOMPADDING', (0, 0), (-1, -1), 6)]))
    story += [head, Spacer(1, 6)]

    # Key facts grid (4 per row)
    meta = [(k, v) for k, v in spec.get('meta', []) if v not in (None, '')]
    if meta:
        cells = [[P(k.upper(), 6.5, 'Poppins-Medium', GREY), P(v, 8.5, 'Poppins-Medium')] for k, v in meta]
        per = 4
        rows = [cells[i:i + per] + [''] * (per - len(cells[i:i + per])) for i in range(0, len(cells), per)]
        grid = Table(rows, colWidths=[width / per] * per)
        grid.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BACKGROUND', (0, 0), (-1, -1), SOFT), ('BOX', (0, 0), (-1, -1), 0.5, LINE),
                                  ('TOPPADDING', (0, 0), (-1, -1), 4), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
        story += [grid, Spacer(1, 6)]

    # Parties
    parties = [p for p in spec.get('parties', []) if p]
    if parties:
        cols = [[P(h.upper(), 6.5, 'Poppins-Medium', GREY)] + [P(l, 8.5, 'Poppins-SemiBold' if i == 0 else 'Poppins') for i, l in enumerate(lines) if l] for h, lines in parties]
        t = Table([cols], colWidths=[width / len(cols)] * len(cols))
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('BOX', (0, 0), (-1, -1), 0.5, LINE), ('INNERGRID', (0, 0), (-1, -1), 0.5, LINE),
                               ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]))
        story += [t, Spacer(1, 8)]

    # Tables
    for sec in spec.get('sections', []):
        if sec.get('title'):
            story.append(P(sec['title'], 9.5, 'Poppins-SemiBold', NAVY))
            story.append(Spacer(1, 3))
        headers, rows = sec['headers'], sec.get('rows') or []
        num = set(sec.get('numeric', ()))
        widths = sec.get('widths')
        colw = [width * w for w in widths] if widths else [width / len(headers)] * len(headers)
        data = [[P(h, 7, 'Poppins-SemiBold', colors.white, TA_RIGHT if i in num else TA_LEFT) for i, h in enumerate(headers)]]
        for r in rows:
            data.append([P(c, 7.8, align=TA_RIGHT if i in num else TA_LEFT) for i, c in enumerate(r)])
        if not rows:
            data.append([P(sec.get('empty', 'Nothing to show.'), 7.8, color=GREY)] + [''] * (len(headers) - 1))
        if sec.get('total_row'):
            data.append([P(c, 7.8, 'Poppins-SemiBold', align=TA_RIGHT if i in num else TA_LEFT) for i, c in enumerate(sec['total_row'])])
        t = Table(data, colWidths=colw, repeatRows=1)
        st = [('BACKGROUND', (0, 0), (-1, 0), NAVY), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LINEBELOW', (0, 1), (-1, -1), 0.4, LINE),
              ('TOPPADDING', (0, 0), (-1, -1), 3), ('BOTTOMPADDING', (0, 0), (-1, -1), 3)]
        if not rows:
            st.append(('SPAN', (0, 1), (-1, 1)))
        if sec.get('total_row'):
            st += [('BACKGROUND', (0, -1), (-1, -1), SOFT), ('LINEABOVE', (0, -1), (-1, -1), 0.8, NAVY)]
        for i in range(1, len(data) - (1 if sec.get('total_row') else 0)):
            if i % 2 == 0:
                st.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FAFBFD')))
        t.setStyle(TableStyle(st))
        story += [t, Spacer(1, 8)]

    # Totals + words
    totals = spec.get('totals') or []
    if totals or spec.get('words'):
        right = Table([[P(l, 8, 'Poppins-SemiBold' if b else 'Poppins', NAVY if b else INK), P(v, 9 if b else 8, 'Poppins-Bold' if b else 'Poppins', NAVY if b else INK, TA_RIGHT)]
                       for l, v, b in totals] or [['', '']], colWidths=[width * 0.22, width * 0.18])
        right.setStyle(TableStyle([('LINEABOVE', (0, -1), (-1, -1), 0.8, NAVY), ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2)]))
        words = [P('AMOUNT IN WORDS', 6.5, 'Poppins-Medium', GREY), P(spec.get('words', ''), 8.5, 'Poppins-Medium')] if spec.get('words') else ''
        t = Table([[words, right]], colWidths=[width * 0.6, width * 0.4])
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 0)]))
        story += [KeepTogether([t]), Spacer(1, 8)]

    for n in spec.get('notes', []):
        if n:
            story.append(P(n, 7.8, color=GREY))
    if spec.get('bank') and org.bank_account_number:
        story.append(P(f'Bank: {org.bank_name} {org.bank_branch} · A/c {org.bank_account_name or org.legal_name} · {org.bank_account_number} · IFSC {org.bank_ifsc}', 7.8))
    terms = spec.get('terms')
    if terms:
        story += [Spacer(1, 4), P('TERMS & CONDITIONS', 6.5, 'Poppins-Medium', GREY), P(terms, 7.2, color=GREY)]
    sigs = spec.get('signatures')
    if sigs:
        last = sigs[-1]
        sig_cells = [[P(s, 7.5, color=GREY) for s in sigs[:-1]] + [P(f'For {org.legal_name}\n\n\n{org.signatory_name or ""} {("· " + org.signatory_designation) if org.signatory_designation else ""}\n{last}', 7.5, align=TA_RIGHT)]]
        t = Table(sig_cells, colWidths=[width / len(sigs)] * len(sigs))
        t.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'BOTTOM'), ('TOPPADDING', (0, 0), (-1, -1), 22)]))
        story += [Spacer(1, 6), KeepTogether([t])]
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    return buf.getvalue()


def response(spec, filename, inline=True):
    data = render(spec)
    resp = HttpResponse(data, content_type='application/pdf')
    resp['Content-Disposition'] = f'{"inline" if inline else "attachment"}; filename="{filename}"'
    resp['X-Content-Type-Options'] = 'nosniff'
    resp['Cache-Control'] = 'private, no-store'
    return resp
