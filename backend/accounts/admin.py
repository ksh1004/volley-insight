from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class VolleyUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("서비스", {"fields": ("nickname",)}),)
