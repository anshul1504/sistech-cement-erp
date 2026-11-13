import re
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver
from django.urls import reverse
from erp.models import AuditEvent


def describe_client(request):
    """Short, non-sensitive description of where a sign-in came from: method · IP · browser on OS."""
    if request is None:
        return ''
    agent = request.META.get('HTTP_USER_AGENT', '')
    browser = next((name for pattern, name in [
        (r'Edg/', 'Edge'), (r'OPR/|Opera', 'Opera'), (r'Firefox/', 'Firefox'),
        (r'Chrome/', 'Chrome'), (r'Safari/', 'Safari')] if re.search(pattern, agent)), 'Browser')
    system = next((name for pattern, name in [
        (r'Android', 'Android'), (r'iPhone|iPad', 'iOS'), (r'Windows', 'Windows'),
        (r'Mac OS X|Macintosh', 'macOS'), (r'Linux', 'Linux')] if re.search(pattern, agent)), 'unknown device')
    if request.path == reverse('otp_verify'):
        method = 'Email + code'
    elif '@' in request.POST.get('username', ''):
        method = 'Email + password'
    else:
        method = 'Username + password'
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    return f'{method} · {ip} · {browser} on {system}'


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    AuditEvent.objects.create(actor=user, action='Account signed in', reference=f'user:{user.pk}', detail=describe_client(request))


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    if user:
        AuditEvent.objects.create(actor=user, action='Account signed out', reference=f'user:{user.pk}')
