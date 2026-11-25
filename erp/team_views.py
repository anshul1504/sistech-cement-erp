"""Sales team charts: leaderboard + trend for managers, and one page per sales person (each person can open their own)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render

from . import team as T
from .insight_views import _ctx


def can_see_team(user):
    return user.has_perm('erp.view_invoice') and (user.has_perm('erp.view_team_crm') or user.has_perm('erp.view_executive_dashboard'))


def can_see_self(user):
    return user.has_perm('erp.create_sales_order') or user.has_perm('erp.view_crmperformance')


@login_required
def sales_team(request):
    if not can_see_team(request.user):
        raise PermissionDenied
    period, sites, ctx = _ctx(request, 'Sales team', 'sales')
    ctx.update(team=T.team(sites, period), team_page=True, area=None)
    return render(request, 'erp/sales_team.html', ctx)


@login_required
def sales_person(request, pk):
    person = get_object_or_404(get_user_model(), pk=pk)
    if not can_see_team(request.user) and not (person == request.user and can_see_self(request.user)):
        raise PermissionDenied
    name = person.get_full_name() or person.get_username()
    period, sites, ctx = _ctx(request, name, 'sales')
    ctx.update(person=person, name=name, data=T.person(person, sites, period), team_page=True, area=None, can_team=can_see_team(request.user))
    return render(request, 'erp/sales_person.html', ctx)
