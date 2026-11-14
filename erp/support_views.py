"""Help & support screens: ticket list (own tickets, or all for the support team), new ticket, conversation and actions."""
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import support as S
from .models import TICKET_CATEGORIES, TICKET_PRIORITIES, TICKET_STATUSES, AuditEvent


def _agents():
    U = get_user_model()
    return [u for u in U.objects.filter(is_active=True).order_by('first_name', 'username') if S.is_agent(u)]


@login_required
def tickets(request):
    u, G = request.user, request.GET
    qs = S.visible(u)
    status, q = G.get('status', 'active'), G.get('q', '').strip()
    if status == 'active': qs = qs.filter(status__in=['open', 'progress', 'waiting'])
    elif status == 'overdue': qs = qs.filter(status__in=['open', 'progress'], due_at__lt=timezone.now())
    elif status == 'mine': qs = qs.filter(assigned_to=u).exclude(status='closed')
    elif status == 'unassigned': qs = qs.filter(assigned_to__isnull=True, status__in=['open', 'progress', 'waiting'])
    elif status != 'all': qs = qs.filter(status=status)
    if q: qs = qs.filter(Q(number__icontains=q) | Q(subject__icontains=q) | Q(raised_by__username__icontains=q) | Q(module__icontains=q))
    return render(request, 'erp/support_list.html', {'title': 'Help & support', 'rows': qs[:200], 'status': status, 'q': q, 'agent': S.is_agent(u),
                                                     'stats': S.stats(u), 'statuses': TICKET_STATUSES})


@login_required
def ticket_new(request):
    P = request.POST
    errors = []
    if request.method == 'POST':
        try:
            t = S.raise_ticket(request.user, P.get('subject'), P.get('description'), P.get('category'), P.get('priority'), P.get('module'), P.get('page_url'))
        except ValidationError as e:
            errors = e.messages
        else:
            messages.success(request, f'{t.number} raised. The support team will reply by {t.due_at:%d %b, %H:%M}.')
            return redirect('ticket_detail', pk=t.pk)
    src = request.GET.get('from', '')
    src = src if src.startswith('/') else ''
    return render(request, 'erp/support_new.html', {'title': 'Raise a support ticket', 'categories': TICKET_CATEGORIES, 'priorities': TICKET_PRIORITIES,
                                                    'errors': errors, 'post': P, 'page_url': P.get('page_url', src), 'module': P.get('module', screen_name(src))})


def screen_name(path):
    """'/orders/12/' → 'Sales orders' using the sidebar labels, so the support team sees which screen the user was on."""
    from django.urls import resolve, Resolver404
    from .context import NAV_GROUPS
    try:
        name = resolve(path).url_name
    except Resolver404:
        return ''
    for _, items in NAV_GROUPS:
        for i in items:
            if name in i['match']:
                return i['label']
    return ''


@login_required
def ticket_detail(request, pk):
    t = get_object_or_404(S.visible(request.user), pk=pk)
    agent = S.is_agent(request.user)
    msgs = t.messages.select_related('author')
    if not agent:
        msgs = msgs.filter(internal=False)
    return render(request, 'erp/support_detail.html', {'title': t.number, 't': t, 'msgs': msgs, 'agent': agent, 'agents': _agents() if agent else [],
                                                       'own': t.raised_by_id == request.user.pk,
                                                       'history': AuditEvent.objects.filter(reference=t.number).select_related('actor').order_by('-created_at')[:10] if agent else []})


@login_required
@require_POST
def ticket_action(request, pk, action):
    get_object_or_404(S.visible(request.user), pk=pk)
    P, u = request.POST, request.user
    try:
        if action == 'reply':
            S.reply(u, pk, P.get('body'), P.get('internal') == '1'); msg = 'Message added.'
        elif action == 'assign':
            S.assign(u, pk, get_user_model().objects.filter(pk=P.get('agent') or 0).first()); msg = 'Assigned.'
        elif action == 'resolve':
            S.resolve(u, pk, P.get('resolution')); msg = 'Marked resolved. The user can accept or reopen it.'
        elif action == 'close':
            S.close(u, pk, int(P['rating']) if P.get('rating', '').isdigit() else None); msg = 'Ticket closed. Thank you for the feedback.'
        elif action == 'reopen':
            S.reopen(u, pk, P.get('reason')); msg = 'Ticket reopened.'
        else:
            raise Http404
        messages.success(request, msg)
    except ValidationError as e:
        messages.error(request, ' '.join(e.messages))
    return redirect('ticket_detail', pk=pk)
