from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import FavoritePlayer, FavoriteTeam, User


class FavoriteTeamInline(admin.TabularInline):
    model = FavoriteTeam
    raw_id_fields = ["team"]
    extra = 0


class FavoritePlayerInline(admin.TabularInline):
    model = FavoritePlayer
    raw_id_fields = ["player"]
    extra = 0


@admin.register(User)
class VolleyUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("서비스", {"fields": ("nickname",)}),)
    list_display = ["email", "nickname", "is_staff", "date_joined", "last_login"]
    search_fields = ["email", "nickname"]
    inlines = [FavoriteTeamInline, FavoritePlayerInline]
