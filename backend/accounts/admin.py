"""관리자 화면: 회원 목록과 회원별 관심 팀·선수(인라인 표)."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import FavoritePlayer, FavoriteTeam, User


class FavoriteTeamInline(admin.TabularInline):
    """회원 화면 안에 표로 보이는 관심 팀(raw_id: 팀을 id로 고르는 칸, 목록이 길어도 빠름)."""
    model = FavoriteTeam
    raw_id_fields = ["team"]
    extra = 0


class FavoritePlayerInline(admin.TabularInline):
    """회원 화면 안에 표로 보이는 관심 선수."""
    model = FavoritePlayer
    raw_id_fields = ["player"]
    extra = 0


@admin.register(User)
class VolleyUserAdmin(UserAdmin):
    """Django 기본 사용자 관리 화면에 닉네임 칸과 관심 목록을 더한 것."""
    fieldsets = UserAdmin.fieldsets + (("서비스", {"fields": ("nickname",)}),)
    list_display = ["email", "nickname", "is_staff", "date_joined", "last_login"]
    search_fields = ["email", "nickname"]
    inlines = [FavoriteTeamInline, FavoritePlayerInline]
