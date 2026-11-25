"""PDF downloads for documents and reports (same permission and plant scope as the screens; every download audited)."""
from django.contrib.auth.decorators import login_required
from django.http import Http404

from . import pdf, pdf_docs
from .models import AuditEvent


def _send(request, spec, filename, ref):
    AuditEvent.objects.create(actor=request.user, action='PDF downloaded', reference=ref[:60], detail=filename[:250])
    return pdf.response(spec, filename, inline=request.GET.get('download') != '1')


@login_required
def document_pdf(request, kind, pk):
    builder = pdf_docs.DOCS.get(kind)
    if not builder:
        raise Http404
    if kind == 'invoice':
        copy = request.GET.get('copy', 'Original for recipient')
        spec, name = builder(request.user, pk, copy if copy in ('Original for recipient', 'Duplicate for transporter', 'Triplicate for supplier') else 'Original for recipient')
    else:
        spec, name = builder(request.user, pk)
    return _send(request, spec, name, f'{kind}:{pk}')


@login_required
def report_pdf(request, kind):
    builder = pdf_docs.REPORTS.get(kind)
    if not builder:
        raise Http404
    spec, name = builder(request.user, request.GET)
    return _send(request, spec, name, kind)
