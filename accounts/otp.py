"""Password-first, session-bound email verification. Raw codes are never stored."""
import secrets
from datetime import timedelta
from smtplib import SMTPException
from email.mime.image import MIMEImage
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.crypto import salted_hmac, constant_time_compare
from erp.models import AuditEvent
from .models import EmailOTPChallenge
from .security import allow_attempt


def fingerprint(value, purpose):
    return salted_hmac('email-otp:' + purpose, value).hexdigest()


def masked_email(email):
    local, domain = email.rsplit('@', 1)
    return local[:2] + '•••@' + domain


def pending_challenge(request):
    try:
        challenge = EmailOTPChallenge.objects.select_related('user').get(pk=request.session.get('otp_challenge'))
    except (EmailOTPChallenge.DoesNotExist, ValidationError, ValueError, TypeError):
        return None
    user = challenge.user
    if (challenge.consumed_at or not user.is_active
            or challenge.password_verified_at < timezone.now()-timedelta(minutes=15)
            or user.email.casefold() != challenge.email.casefold()
            or not constant_time_compare(challenge.session_hash, fingerprint(request.session.session_key or '', 'session'))
            or not constant_time_compare(challenge.credential_hash, fingerprint(user.password, 'password'))):
        return None
    return challenge


def issue_challenge(request, user, password_verified_at=None):
    email = user.email.strip()
    validate_email(email)
    if email.lower().endswith(('.test', '.invalid', '.example')):
        raise ValidationError('Your account needs a real email address. Ask your administrator to update it before signing in.')
    if get_user_model().objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        raise ValidationError('Your account email needs administrator review before you can sign in.')
    if not allow_attempt(request, 'otp-send', str(user.pk)):
        raise ValidationError('Too many code requests. Wait 15 minutes before trying again.')
    now = timezone.now()
    code = f'{secrets.randbelow(1000000):06d}'
    with transaction.atomic():
        locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
        if not locked_user.is_active or locked_user.password != user.password or locked_user.email != user.email:
            raise ValidationError('Your account changed. Please sign in again.')
        if EmailOTPChallenge.objects.filter(user=user, created_at__gt=now-timedelta(seconds=settings.EMAIL_OTP_RESEND_SECONDS)).exists():
            raise ValidationError('Please wait 60 seconds before requesting another code.')
        EmailOTPChallenge.objects.filter(user=user, consumed_at__isnull=True).update(consumed_at=now)
        challenge = EmailOTPChallenge.objects.create(
            user=user, email=email, code_hash=make_password(code),
            session_hash=fingerprint(request.session.session_key, 'session'),
            credential_hash=fingerprint(user.password, 'password'),
            password_verified_at=password_verified_at or now,
            expires_at=now+timedelta(seconds=settings.EMAIL_OTP_TTL_SECONDS),
        )
    logo_path = settings.BASE_DIR / 'static' / 'brand' / 'sistech-logo-email.png'
    logo_bytes = logo_path.read_bytes() if logo_path.is_file() else None
    context = {'code': code, 'name': user.first_name or user.username, 'minutes': settings.EMAIL_OTP_TTL_SECONDS // 60, 'has_brand_logo': bool(logo_bytes)}
    message = EmailMultiAlternatives('Your Sistech ERP sign-in code', render_to_string('emails/login_otp.txt', context), settings.DEFAULT_FROM_EMAIL, [email])
    message.attach_alternative(render_to_string('emails/login_otp.html', context), 'text/html')
    if logo_bytes:
        message.mixed_subtype = 'related'
        logo = MIMEImage(logo_bytes, _subtype='png')
        logo.add_header('Content-ID', '<sistech-brand>')
        logo.add_header('Content-Disposition', 'inline', filename='sistech-logo.png')
        message.attach(logo)
    try:
        if message.send(fail_silently=False) != 1:
            raise OSError('Email transport did not accept the message')
    except (SMTPException, OSError):
        EmailOTPChallenge.objects.filter(pk=challenge.pk).update(consumed_at=timezone.now())
        AuditEvent.objects.create(actor=user, action='OTP delivery failed', reference=f'user:{user.pk}')
        raise ValidationError('We couldn’t send your code. Please try again in a minute or contact your administrator.') from None
    EmailOTPChallenge.objects.filter(pk=challenge.pk).update(delivered=True)
    challenge.delivered = True
    AuditEvent.objects.create(actor=user, action='Sign-in code sent', reference=f'user:{user.pk}')
    return challenge


@transaction.atomic
def verify_challenge(request, code):
    pending = pending_challenge(request)
    if not pending:
        return None, 'Your sign-in session has ended. Start again.'
    # Match issuance lock order (user, then challenge) to avoid resend deadlocks.
    current_user = get_user_model().objects.select_for_update().get(pk=pending.user_id)
    challenge = EmailOTPChallenge.objects.select_for_update().select_related('user').get(pk=pending.pk)
    now = timezone.now()
    if (not current_user.is_active or current_user.email.casefold() != challenge.email.casefold()
            or not constant_time_compare(challenge.credential_hash, fingerprint(current_user.password, 'password'))):
        return None, 'Your account changed. Start signing in again.'
    if (challenge.consumed_at or not challenge.delivered or challenge.expires_at <= now
            or challenge.attempts >= settings.EMAIL_OTP_MAX_ATTEMPTS):
        return None, 'This code has expired or reached its attempt limit. Request a new code.'
    challenge.attempts += 1
    accepted = check_password(code, challenge.code_hash)
    if accepted:
        challenge.consumed_at = now
    challenge.save(update_fields=['attempts', 'consumed_at'])
    if not accepted:
        remaining = settings.EMAIL_OTP_MAX_ATTEMPTS-challenge.attempts
        return None, f'That code is incorrect. {remaining} attempts remaining.'
    AuditEvent.objects.create(actor=challenge.user, action='Sign-in code verified', reference=f'user:{challenge.user_id}')
    return current_user, ''
