"""Resolve a journal's source to the existing permission-checked document view."""
from django.urls import reverse


def document_link(entry, user):
    if entry.year_close_id and user.has_perm('erp.view_financialyearclose'):
        return dict(label='Year-end close', url=reverse('year_detail', args=[entry.year_close_id]))
    source = entry.source or ''
    if ':' not in source:
        return None
    prefix, raw = source.split(':', 1)
    if not raw.isdigit():
        return None
    routes = {
        'inv': ('invoice_detail', 'view_invoice', 'Tax invoice'),
        'cn': ('credit_note_detail', 'view_creditnote', 'Credit note'),
        'sinv': ('sinv_detail', 'view_supplierinvoice', 'Supplier invoice'),
        'dn': ('dn_detail', 'view_debitnote', 'Debit note'),
        'frb': ('freight_bill_detail', 'view_freightbill', 'Freight bill'),
        'pay': ('payment_detail', 'view_supplierpayment', 'Supplier payment'),
    }
    if prefix in routes:
        route, permission, label = routes[prefix]
        if user.has_perm('erp.' + permission):
            return dict(label=label, url=reverse(route, args=[int(raw)]))
    if prefix == 'rct' and user.has_perm('erp.view_receipt'):
        from .models import Receipt
        receipt = Receipt.objects.filter(pk=raw).first()
        if receipt:
            return dict(label=receipt.number, url=reverse('partner_ledger', args=[receipt.partner_id]))
    if prefix == 'stk' and user.has_perm('erp.view_stockbalance'):
        from .models import StockMovement
        movement = StockMovement.objects.filter(pk=raw).first()
        if movement:
            return dict(label='Stock card', url=reverse('stock_card', args=[movement.site_id, movement.product_id]))
    if prefix == 'fa' and user.has_perm('erp.view_fixedasset'):
        return dict(label='Fixed asset register', url=reverse('fixed_assets'))
    return None
