from django.db import connection
from rest_framework.decorators import api_view
from rest_framework.response import Response


@api_view(["GET"])
def health(request):
    """Liveness check used by the dev environment and later by Nginx/Kibana."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        db = "ok"
    except Exception as exc:  # report, do not crash the health endpoint
        db = f"error: {exc.__class__.__name__}"
    status = 200 if db == "ok" else 503
    return Response({"status": "ok" if status == 200 else "degraded", "database": db}, status=status)
