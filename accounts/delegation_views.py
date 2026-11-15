from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from erp.models import Site
from . import delegation as D
from .forms import style_fields
from .models import ApprovalDelegation


class DelegationForm(forms.Form):
    principal = forms.ModelChoiceField(queryset=get_user_model().objects.filter(is_active=True).order_by('username'))
    deputy = forms.ModelChoiceField(queryset=get_user_model().objects.filter(is_active=True).order_by('username'))
    permission = forms.ChoiceField(label='Approval action', choices=[(k, v[0]) for k, v in D.POLICIES.items()])
    site = forms.ModelChoiceField(queryset=Site.objects.filter(active=True).order_by('code'), required=False,
        help_text='Select a site for site approvals. Leave blank for company-wide finance approvals.')
    starts_at = forms.DateTimeField(label='Starts at', required=False, input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
        help_text='Asia/Kolkata time. Leave blank to start now.')
    ends_at = forms.DateTimeField(label='Ends at', input_formats=['%Y-%m-%dT%H:%M'],
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}), help_text='Asia/Kolkata time. Authority expires exactly at this time.')
    reason = forms.CharField(max_length=250, widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_fields(self)


def visible(user):
    qs = ApprovalDelegation.objects.select_related('principal', 'deputy', 'site', 'created_by', 'revoked_by')
    return qs if user.is_superuser else qs.filter(Q(principal=user) | Q(deputy=user))


def decorate(grant, now):
    grant.action_label = D.POLICIES.get(grant.permission, (grant.permission,))[0]
    grant.state = 'Revoked' if grant.revoked_at else 'Expired' if grant.ends_at <= now else 'Scheduled' if grant.starts_at > now else 'In window'
    return grant


@login_required
def delegations(request):
    qs = visible(request.user).annotate(use_count=Count('uses')).order_by('-created_at', '-pk')
    status = request.GET.get('status', 'all')
    now = timezone.now()
    if status == 'current':
        qs = qs.filter(revoked_at__isnull=True, starts_at__lte=now, ends_at__gt=now)
    page = Paginator(qs, 30).get_page(request.GET.get('page'))
    for grant in page:
        decorate(grant, now)
    return render(request, 'accounts/delegations.html', dict(title='Approval delegations', page=page, status=status))


@login_required
def delegation_detail(request, pk):
    grant = decorate(get_object_or_404(visible(request.user), pk=pk), timezone.now())
    # Participation does not confer access to business documents referenced by past uses.
    uses = grant.uses.select_related('actor')[:100] if request.user.is_superuser else []
    return render(request, 'accounts/delegation_detail.html', dict(title=str(grant), grant=grant, uses=uses))


@login_required
def delegation_new(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    form = DelegationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = dict(form.cleaned_data)
        data['starts_at'] = data['starts_at'] or timezone.now()
        try:
            grant = D.create(request.user, **data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Approval delegation recorded. Dates and existing scope are checked on every use.')
            return redirect('delegation_detail', pk=grant.pk)
    return render(request, 'accounts/delegation_form.html', dict(title='Delegate approval authority', form=form))


@login_required
@require_POST
def delegation_revoke(request, pk):
    if not request.user.is_superuser:
        raise PermissionDenied
    get_object_or_404(visible(request.user), pk=pk)
    try:
        D.revoke(request.user, pk, request.POST.get('reason', ''))
    except ValidationError as error:
        messages.error(request, ' '.join(error.messages))
    else:
        messages.success(request, 'Delegation revoked; earlier approvals remain in history.')
    return redirect('delegation_detail', pk=pk)
