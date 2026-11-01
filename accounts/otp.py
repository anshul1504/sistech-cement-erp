"""Email OTP scaffold — intentionally inactive. No issue/verify routes exist.

Add the challenge model and tests, then configure delivery and review the
two-step login flow before uncommenting. Password login works without OTP.
"""

# Future model: EmailOTPChallenge
# id: UUID; user: FK; purpose: login / sensitive_action
# code_hash: Django make_password(code), never raw OTP
# session_key_hash: bind to the pre-authenticated session
# expires_at: now + 5 minutes; attempts: 0; consumed_at: nullable
# last_sent_at: enforce a 60-second resend cooldown
#
# import secrets
# from django.contrib.auth.hashers import make_password, check_password
# from django.core.mail import send_mail
# from django.db import transaction
# from django.utils import timezone
# from datetime import timedelta
#
# @transaction.atomic
# def issue_email_otp(user, session_key_hash):
#     # Only after password validation; do NOT log the user in yet.
#     # Lock user/challenge; validate active status, verified email, cooldown,
#     # per-user + per-IP limits, and invalidate previous challenges.
#     code = f'{secrets.randbelow(1_000_000):06d}'
#     challenge = EmailOTPChallenge.objects.create(
#         user=user, purpose='login', code_hash=make_password(code),
#         session_key_hash=session_key_hash,
#         expires_at=timezone.now() + timedelta(minutes=5),
#     )
#     # Queue after commit. Never log the code or return it to the browser.
#     # Delivery failures must not authenticate the user or leak accounts.
#     transaction.on_commit(lambda: send_mail(
#         'Your Sistech sign-in code', f'Your code is {code}. It expires in 5 minutes.',
#         None, [user.email], fail_silently=False,
#     ))
#     return challenge.pk
#
# @transaction.atomic
# def verify_email_otp(challenge_id, code, session_key_hash):
#     challenge = EmailOTPChallenge.objects.select_for_update().get(pk=challenge_id)
#     if (challenge.consumed_at or challenge.expires_at <= timezone.now()
#             or challenge.attempts >= 5 or not challenge.user.is_active
#             or challenge.session_key_hash != session_key_hash):
#         return None
#     challenge.attempts += 1
#     accepted = check_password(code, challenge.code_hash)
#     if accepted:
#         challenge.consumed_at = timezone.now()
#     challenge.save(update_fields=['attempts', 'consumed_at'])
#     # Caller rotates the session and logs in the returned user after success.
#     return challenge.user if accepted else None
#
# Future routes — disabled until the complete flow is reviewed and tested:
# path('otp/verify/', OTPVerifyView.as_view(), name='otp_verify')
# path('otp/resend/', OTPResendView.as_view(), name='otp_resend')
