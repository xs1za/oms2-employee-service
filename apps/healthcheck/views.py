import socket
from urllib.parse import urlparse

from django.conf import settings
from django.db import connection
from django.http import JsonResponse


def check_database() -> dict:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}


def check_tcp_endpoint(endpoint: str, timeout: float = 2.0) -> dict:
    target = endpoint.split(",", 1)[0].strip()
    parsed = urlparse(target if "://" in target else f"tcp://{target}")
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return {"status": "failed", "error": f"Invalid endpoint: {endpoint}"}
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {"status": "ok", "endpoint": f"{host}:{port}"}
    except OSError as exc:
        return {"status": "failed", "endpoint": f"{host}:{port}", "error": str(exc)}


def health(request):
    return JsonResponse({"status": "ok", "service": "OMS2"})


def live(request):
    return JsonResponse({"status": "ok", "service": "OMS2", "checks": {"app": {"status": "ok"}}})


def ready(request):
    checks = {
        "database": check_database(),
        "kafka": check_tcp_endpoint(settings.KAFKA_BOOTSTRAP_SERVERS),
    }
    ready_status = "ok" if all(check["status"] == "ok" for check in checks.values()) else "failed"
    return JsonResponse(
        {"status": ready_status, "service": "OMS2", "checks": checks},
        status=200 if ready_status == "ok" else 503,
    )
