"""Analysis APIs (design.md 1.11)."""
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from league.queries import gender_param, resolve_season
from matches.views import SOURCE

from .queries import POSITION_NAMES, career_board, rating_board
from .verification import summary


class PredictionCheckView(APIView):
    """GET /api/predictions?gender=M|F: backtest by season vs Elo, calibration, this season's results (ANL-10)."""

    def get(self, request):
        return Response({**summary(gender_param(request.query_params.get("gender"))), "source": SOURCE})


class RatingBoardView(APIView):
    """GET /api/ratings?season=&gender=M|F&position=OH|OP|MB|S|L&all=1: position ratings of one season ranked by
    overall (player cards; spark_jobs/player_ratings.py). Without all=1 only qualified players are listed.
    scope=career: the all-time board of career cards instead (season ignored; ranked = 3+ qualified seasons)."""

    def get(self, request):
        p = request.query_params
        season = resolve_season(p.get("season"))
        gender = gender_param(p.get("gender"), required=True)
        position = p.get("position", "OH")
        if position not in POSITION_NAMES:
            raise ValidationError({"position": sorted(POSITION_NAMES)})
        if p.get("scope") == "career":
            board = career_board(gender, position, include_all=p.get("all") == "1")
            return Response({"scope": "career", "season": None, "gender": gender, **board,
                             "scale": "통산 능력치 = 시즌별 점수(그 시기 같은 성별·포지션 평균 60, 표준편차 1당 12점)를 출전 "
                                      "세트 수로 가중 평균. 역대 순위는 순위 자격 시즌이 3번 이상인 선수끼리.",
                             "source": SOURCE})
        board = rating_board(season, gender, position, include_all=p.get("all") == "1")
        return Response({"scope": "season","season": {"code": season.code, "years": season.years}, "gender": gender, **board,
                         "scale": "같은 성별·포지션의 순위 자격 선수(그 시즌과 앞 두 시즌) 평균이 60, 표준편차 1당 12점, 1~99. "
                                  "종합은 능력치 점수의 가중 평균.",
                         "source": SOURCE})
