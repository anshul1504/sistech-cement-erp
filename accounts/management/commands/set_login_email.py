from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db.models import Q
from erp.models import AuditEvent

class Command(BaseCommand):
    help = 'Configure a real, unique OTP email for an existing account using administrator shell access.'

    def add_arguments(self, parser):
        parser.add_argument('username')
        parser.add_argument('email')

    def handle(self, *args, **options):
        email = options['email'].strip().lower()
        try:
            validate_email(email)
            user = get_user_model().objects.get(username=options['username'])
        except (ValidationError, get_user_model().DoesNotExist):
            raise CommandError('Provide an existing username and valid email address.') from None
        if email.endswith(('.test', '.invalid', '.example')):
            raise CommandError('A real deliverable email address is required for OTP login.')
        if get_user_model().objects.exclude(pk=user.pk).filter(Q(email__iexact=email) | Q(username__iexact=email)).exists():
            raise CommandError('This email is already assigned to another user.')
        user.email = email
        user.save(update_fields=['email'])
        AuditEvent.objects.create(actor=user, action='OTP email configured from admin shell', reference=f'user:{user.pk}')
        self.stdout.write(self.style.SUCCESS('Account OTP email updated. No email sent by this command.'))
