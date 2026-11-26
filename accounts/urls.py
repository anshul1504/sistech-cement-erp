from django.urls import path
from django.contrib.auth import views as auth
from django.views.generic import TemplateView, RedirectView
from . import auth_views as av, views as v, account_views as acc

from .reauth_views import reauthenticate
from . import delegation_views as dv
from . import handoff_views as hv
from . import dealer_access_views as dav
from . import driver_access_views as drv

urlpatterns = [
    path('approval-inbox/', hv.inbox, name='approval_inbox'),
    path('access/approval-routing/', hv.board, name='approval_board'),
    path('access/approval-routing/<str:kind>/<int:pk>/assign/', hv.assignment, name='approval_assign'),
    path('access/handoff/', hv.handoff, name='work_handoff'),
    path('access/handoff/<int:pk>/', hv.handoff_detail, name='work_handoff_detail'),
    path('access/delegations/', dv.delegations, name='delegations'),
    path('access/delegations/new/', dv.delegation_new, name='delegation_new'),
    path('access/delegations/<int:pk>/', dv.delegation_detail, name='delegation_detail'),
    path('access/delegations/<int:pk>/revoke/', dv.delegation_revoke, name='delegation_revoke'),
    path('confirm-identity/', reauthenticate, name='reauthenticate'),
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
    # Administration: users
    path('access/', v.access_control, name='access_control'),
    path('access/users/new/', v.member_edit, name='member_new'),
    path('access/users/<int:pk>/', v.member_detail, name='member_detail'),
    path('access/users/<int:pk>/dealer-access/', dav.dealer_access, name='dealer_access'),
    path('access/users/<int:pk>/driver-access/', drv.driver_access, name='driver_access'),
    path('access/users/<int:pk>/edit/', v.member_edit, name='member_edit'),
    path('access/users/<int:pk>/status/', v.member_status, name='member_status'),
    path('access/users/<int:pk>/sign-out/', v.member_sign_out, name='member_sign_out'),
    path('access/users/<int:pk>/reset-link/', v.member_reset_link, name='member_reset_link'),
    path('access/users/<int:pk>/set-password/', v.member_set_password, name='member_set_password'),
    # Administration: roles & permissions
    path('access/roles/', v.roles, name='roles'),
    path('access/roles/new/', v.role_edit, name='role_new'),
    path('access/roles/<int:pk>/', v.role_detail, name='role_detail'),
    path('access/roles/<int:pk>/edit/', v.role_edit, name='role_edit'),
    path('access/roles/<int:pk>/duplicate/', v.role_duplicate, name='role_duplicate'),
    path('access/roles/<int:pk>/delete/', v.role_delete, name='role_delete'),
    # Administration: system
    path('system/', v.system_settings, name='system_settings'),
    path('system/test-email/', v.system_test_email, name='system_test_email'),
]
