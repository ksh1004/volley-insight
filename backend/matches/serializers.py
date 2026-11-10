"""경기 목록 API의 응답 모양(직렬화기)과 경기 키·팀 참조 도우미."""
from rest_framework import serializers

from .models import Match


def match_key(m: Match) -> str:
    """바뀌지 않는 공개용 경기 id: 시즌 코드 + KOVO 경기 번호, 예: 022-3(화면 주소와 API에 씀. DB id는 노출하지 않음)."""
    return f"{m.competition.season.code}-{m.game_no}"


def team_ref(team):
    """응답에 넣을 팀 참조(코드와 이름만)."""
    return {"code": team.code, "name": team.name}


class MatchListSerializer(serializers.ModelSerializer):
    """경기 목록의 한 행: 경기 키, 시즌, 대회, 일시, 장소, 상태, 두 팀, 세트 스코어, 세트별 점수, 최신 예측.
    source="a.b"는 관련 객체의 필드를 꺼내 쓰는 것, SerializerMethodField는 아래 get_* 메서드가 값을 만든다."""
    key = serializers.SerializerMethodField()
    season = serializers.CharField(source="competition.season.code")
    season_years = serializers.CharField(source="competition.season.years")
    league_code = serializers.CharField(source="competition.league_code")
    competition = serializers.CharField(source="competition.name")
    home = serializers.SerializerMethodField()
    away = serializers.SerializerMethodField()
    sets = serializers.SerializerMethodField()
    prediction = serializers.SerializerMethodField()

    class Meta:
        model = Match
        fields = ["key", "season", "season_years", "league_code", "competition", "round", "gender", "scheduled_at", "venue",
                  "broadcasters", "status", "home", "away", "home_sets", "away_sets", "sets", "prediction"]

    def get_key(self, m):
        return match_key(m)

    def get_home(self, m):
        return team_ref(m.home_team)

    def get_away(self, m):
        return team_ref(m.away_team)

    def get_prediction(self, m):
        """Latest pre-match prediction (prefetched in base_matches)."""
        p = next(iter(m.predictions.all()), None)
        if not p:
            return None
        return {"home_win_prob": float(p.home_win_prob), "pred_sets": [p.pred_home_sets, p.pred_away_sets],
                "is_correct": p.is_correct}

    def get_sets(self, m):
        """세트별 [홈 점수, 원정 점수] 목록(세트는 base_matches에서 미리 가져옴)."""
        return [[s.home_points, s.away_points] for s in m.sets.all()]
