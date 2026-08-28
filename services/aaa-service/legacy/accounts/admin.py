from django import forms
from django.contrib import admin

from common.passwords import hash_password

from .models import AdminUser


class AdminUserAdminForm(forms.ModelForm):
    password = forms.CharField(
        required=False,
        widget=forms.PasswordInput,
        help_text="Enter a new plaintext password to set/replace it. Leave blank to keep the current one.",
    )

    class Meta:
        model = AdminUser
        exclude = ("password_hash",)


@admin.register(AdminUser)
class AdminUserAdmin(admin.ModelAdmin):
    form = AdminUserAdminForm
    list_display = ("email", "name", "role", "is_active", "last_login_at")
    list_filter = ("role", "is_active")
    search_fields = ("email", "name")

    def save_model(self, request, obj, form, change):
        password = form.cleaned_data.get("password")
        if password:
            obj.password_hash = hash_password(password)
        elif not change:
            raise forms.ValidationError("A password is required when creating an admin user.")
        super().save_model(request, obj, form, change)
