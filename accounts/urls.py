from django.urls import path
from django.contrib.auth import views as auth
from django.views.generic import TemplateView, RedirectView
from . import auth_views as av, views as v, account_views as acc

urlpatterns = [
    path('login/', av.SignInView.as_view(), name='login'),
    path('otp/verify/', av.OTPVerifyView.as_view(), name='otp_verify'),
    path('otp/resend/', av.otp_resend, name='otp_resend'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('signed-out/', TemplateView.as_view(template_name='registration/logged_out.html'), name='signed_out'),
    path('access-help/', TemplateView.as_view(template_name='registration/access_help.html'), name='access_help'),
    path('forgot-password/', av.RecoveryView.as_view(), name='password_reset'),
    path('forgot-password/sent/', auth.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', av.ResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/complete/', auth.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('password/change/', acc.SecurityView.as_view(), name='password_change'),
    path('password/changed/', RedirectView.as_view(pattern_name='account_security'), name='password_change_done'),
    path('profile/', acc.profile, name='profile'),
    path('profile/edit/', acc.profile_edit, name='profile_edit'),
    path('profile/security/', acc.SecurityView.as_view(), name='account_security'),
    path('profile/activity/', acc.activity, name='account_activity'),
    path('profile/sessions/<str:sid>/sign-out/', acc.session_revoke, name='session_revoke'),
    path('profile/sign-out-others/', acc.sign_out_other_sessions, name='sign_out_other_sessions'),
    path('profile/sign-out-older/', acc.sign_out_legacy_sessions, name='sign_out_legacy_sessions'),
    path('profile/photo/<int:user_id>/', acc.photo, name='profile_photo'),
    path('profile/contacts/', acc.contacts, name='account_contacts'),
    path('profile/contacts/new/', acc.contact_form, name='contact_new'),
    path('profile/contacts/<int:pk>/edit/', acc.contact_form, name='contact_edit'),
    path('profile/contacts/<int:pk>/delete/', acc.contact_delete, name='contact_delete'),
    path('access/', v.access_control, name='access_control'),
    path('access/roles/new/', v.role_edit, name='role_new'),
    path('access/roles/<int:pk>/', v.role_edit, name='role_edit'),
    path('access/users/new/', v.member_edit, name='member_new'),
    path('access/users/<int:pk>/', v.member_edit, name='member_edit'),
]
