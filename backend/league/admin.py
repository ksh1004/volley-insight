"""관리자 화면: Spark 파이프라인이 쓰는 기준 데이터(시즌, 대회, 팀, 선수)를 읽기 전용으로 보여 준다.
고치려면 '데이터 보정' 화면을 쓴다(다음 정제 때 적용되어 다시 수집해도 유지됨)."""
from django.contrib import admin

from matches.admin import ReadOnlyAdmin

from .models import Competition, Player, Season, Team


@admin.register(Season)
class SeasonAdmin(ReadOnlyAdmin):
    list_display = ("code", "name", "start_date", "end_date")


@admin.register(Competition)
class CompetitionAdmin(ReadOnlyAdmin):
    list_display = ("season", "gcode", "league_code", "type", "name")
    list_filter = ("season", "type")


@admin.register(Team)
class TeamAdmin(ReadOnlyAdmin):
    list_display = ("code", "name", "full_name", "gender", "home_arena", "is_active", "last_seen_on")
    list_filter = ("gender", "is_active")


@admin.register(Player)
class PlayerAdmin(ReadOnlyAdmin):
    list_display = ("code", "name", "gender", "position", "height_cm", "last_seen_on")
    list_filter = ("gender", "position")
    search_fields = ("name", "code")
