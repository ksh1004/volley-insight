"""Analysis APIs (design.md 1.11)."""
from rest_framework.response import Response
from rest_framework.views import APIView

from league.queries import gender_param
from matches.views import SOURCE

from .verification import summary


class PredictionCheckView(APIView):
    """GET /api/predictions?gender=M|F: backtest by season vs Elo, calibration, this season's results (ANL-10)."""

    def get(self, request):
        return Response({**summary(gender_param(request.query_params.get("gender"))), "source": SOURCE})
