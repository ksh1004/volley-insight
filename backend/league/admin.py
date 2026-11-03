"""Read-only admin views for reference data written by the Spark pipeline."""
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
