from django.conf import settings
from django.core.management.base import BaseCommand

from common.passwords import hash_password

from ...models import AdminUser


class Command(BaseCommand):
    help = "Idempotently creates the default super_admin if one doesn't already exist."

    def handle(self, *args, **options):
        email = settings.DEFAULT_ADMIN_EMAIL.lower().strip()

        if AdminUser.objects.filter(email=email).exists():
            self.stdout.write(f"Default admin already exists for {email}")
            return

        AdminUser.objects.create(
            name="Platform Administrator",
            email=email,
            password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
            role=AdminUser.Role.SUPER_ADMIN,
            is_active=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Default admin created for {email}"))
