"""상태 확인 API(/api/health)."""
from django.db import connection
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    """서버가 살아 있고 DB에 접속되는지 확인한다(개발 환경 점검, 모니터링용). DB가 안 되면 503."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        db = "ok"
    except Exception as exc:  # 오류를 알리기만 하고 상태 확인 API 자체는 죽지 않는다
        db = f"error: {exc.__class__.__name__}"
    status = 200 if db == "ok" else 503
    return Response({"status": "ok" if status == 200 else "degraded", "database": db}, status=status)
