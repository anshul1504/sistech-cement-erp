from django.urls import path
from django.contrib.auth import views as auth
from django.views.generic import TemplateView
from . import auth_views as av, views as v

urlpatterns = [
    path('login/', av.SignInView.as_view(), name='login'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('signed-out/', TemplateView.as_view(template_name='registration/logged_out.html'), name='signed_out'),
    path('access-help/', TemplateView.as_view(template_name='registration/access_help.html'), name='access_help'),
    path('forgot-password/', av.RecoveryView.as_view(), name='password_reset'),
    path('forgot-password/sent/', auth.PasswordResetDoneView.as_view(), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', av.ResetConfirmView.as_view(), name='password_reset_confirm'),
    path('reset/complete/', auth.PasswordResetCompleteView.as_view(), name='password_reset_complete'),
    path('password/change/', av.ChangePasswordView.as_view(), name='password_change'),
    path('password/changed/', auth.PasswordChangeDoneView.as_view(template_name='registration/password_change_done.html'), name='password_change_done'),
    path('profile/', av.profile, name='profile'),
    path('access/', v.access_control, name='access_control'),
    path('access/roles/new/', v.role_edit, name='role_new'),
    path('access/roles/<int:pk>/', v.role_edit, name='role_edit'),
    path('access/users/new/', v.member_edit, name='member_new'),
    path('access/users/<int:pk>/', v.member_edit, name='member_edit'),
]
