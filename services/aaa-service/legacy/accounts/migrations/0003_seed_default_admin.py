from django.conf import settings
from django.db import migrations

from common.passwords import hash_password


def seed_default_admin(apps, schema_editor):
    AdminUser = apps.get_model("accounts", "AdminUser")

    email = settings.DEFAULT_ADMIN_EMAIL.lower().strip()
    if AdminUser.objects.filter(email=email).exists():
        return

    AdminUser.objects.create(
        name="Platform Administrator",
        email=email,
        password_hash=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
        role="super_admin",
        is_active=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_adminuser_branch_adminuser_franchise_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_default_admin, reverse_code=migrations.RunPython.noop),
    ]
