"""관리자 화면: 정제된 경기 데이터를 살펴보는 읽기 전용 화면들. 행은 Spark 파이프라인이 쓰며 여기서 고치지 않는다
(고칠 때는 '데이터 보정'을 쓴다)."""
from django.contrib import admin

from .models import Match, PlayerMatchStat, RallyEvent, SetScore, TeamMatchStat


class ReadOnlyAdmin(admin.ModelAdmin):
    """추가·수정·삭제 권한을 모두 막은 관리자 화면 기본형(다른 앱의 읽기 전용 화면도 이것을 상속한다)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SetScoreInline(admin.TabularInline):
    """경기 화면 안에 표로 보이는 세트 점수."""
    model = SetScore
    extra = 0
    can_delete = False
    fields = ("set_no", "home_points", "away_points", "duration_min")
    readonly_fields = fields


@admin.register(Match)
class MatchAdmin(ReadOnlyAdmin):
    list_display = ("scheduled_at", "competition", "game_no", "home_team", "away_team", "home_sets", "away_sets", "status")
    list_filter = ("competition__season", "gender", "status", "competition__league_code")
    search_fields = ("home_team__name", "away_team__name")
    list_select_related = ("competition__season", "home_team", "away_team")  # 목록에서 관련 행을 한 번에 가져와 쿼리 수를 줄임
    inlines = [SetScoreInline]


@admin.register(TeamMatchStat)
class TeamMatchStatAdmin(ReadOnlyAdmin):
    list_display = ("match", "team", "points", "attack_success", "block_success", "serve_aces", "opponent_errors")
    list_select_related = ("match__competition__season", "match__home_team", "match__away_team", "team")


@admin.register(PlayerMatchStat)
class PlayerMatchStatAdmin(ReadOnlyAdmin):
    list_display = ("match", "player", "team", "position", "points", "attack_success", "attack_attempts", "played_sets")
    search_fields = ("player__name",)
    list_select_related = ("match__competition__season", "match__home_team", "match__away_team", "player", "team")


@admin.register(RallyEvent)
class RallyEventAdmin(ReadOnlyAdmin):
    list_display = ("match", "set_no", "event_no", "rally_no", "team", "player", "action_code", "result_code",
                    "grade", "home_score", "away_score")
    list_select_related = ("match__competition__season", "match__home_team", "match__away_team", "team", "player")
