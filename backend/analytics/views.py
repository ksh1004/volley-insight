"""분석 API(설계서 1.11): 예측 검증, 포지션별 능력치 순위."""
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from league.queries import gender_param, resolve_season
from matches.views import SOURCE

from .queries import POSITION_NAMES, career_board, rating_board
from .verification import summary


class PredictionCheckView(APIView):
    """GET /api/predictions?gender=M|F: 예측 검증 화면 데이터(ANL-10) — 시즌별 백테스트(Elo와 비교), 보정 곡선,
    이번 시즌 실제 경기 전 예측의 적중 결과. 계산은 verification.summary()."""

    def get(self, request):
        return Response({**summary(gender_param(request.query_params.get("gender"))), "source": SOURCE})


class RatingBoardView(APIView):
    """GET /api/ratings?season=&gender=M|F&position=OH|OP|MB|S|L&all=1&scope=career
    포지션별 능력치 순위(선수 카드, spark_jobs/player_ratings.py). 기본은 한 시즌을 종합 점수 순으로.
    all=1이 아니면 순위 자격 선수만 나온다. scope=career면 시즌 대신 역대 통산 순위(순위 자격 시즌 3번 이상만 순위).
    응답의 scale은 점수 눈금 설명 문구(화면 아래 안내에 그대로 쓴다)."""

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
        return Response({"scope": "season", "season": {"code": season.code, "years": season.years}, "gender": gender, **board,
                         "scale": "같은 성별·포지션의 순위 자격 선수(그 시즌과 앞 두 시즌) 평균이 60, 표준편차 1당 12점, 1~99. "
                                  "종합은 능력치 점수의 가중 평균.",
                         "source": SOURCE})
