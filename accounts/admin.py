from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class PulseUserAdmin(UserAdmin):
    list_display = ("username", "email", "role", "is_active", "created_at")
    list_filter = ("role", "is_active")
    fieldsets = UserAdmin.fieldsets + (("Pulse", {"fields": ("role",)}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Pulse", {"fields": ("email", "role")}),)
