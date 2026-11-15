from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login


class RequireVerifiedEmailSessionMiddleware:
    """Old password-only sessions must complete the newly enabled second step."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .security import access_allowed
        if request.user.is_authenticated and not access_allowed(request.user):
            logout(request)
            return redirect_to_login(request.get_full_path())
        username_session = (settings.EMAIL_OTP_SKIP_FOR_USERNAME
                            and request.user.is_authenticated
                            and request.session.get('password_login_user') == str(request.user.pk))
        if (settings.EMAIL_OTP_ENABLED and request.user.is_authenticated and not username_session
                and (request.session.get('email_otp_verified_user') != str(request.user.pk)
                     or request.session.get('email_otp_verified_email') != request.user.email.strip().casefold())):
            logout(request)
            return redirect_to_login(request.get_full_path())
        return self.get_response(request)
