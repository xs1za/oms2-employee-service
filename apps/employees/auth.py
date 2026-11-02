import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import HttpRequest, JsonResponse


def verified_principal(request: HttpRequest) -> tuple[dict | None, JsonResponse | None]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None, JsonResponse({"detail": {"code": "invalid_token", "message": "Bearer token is required"}}, status=401)

    request_obj = Request(
        f"{settings.OMS1_AUTH_BASE_URL.rstrip('/')}/auth/verify",
        headers={"Authorization": authorization, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request_obj, timeout=3) as response:
            principal = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return None, JsonResponse({"detail": {"code": "invalid_token", "message": "Token is invalid or revoked"}}, status=401)
    except (URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None, JsonResponse({"detail": {"code": "auth_service_unavailable", "message": "Authorization service is unavailable"}}, status=503)

    if principal.get("active") is not True:
        return None, JsonResponse({"detail": {"code": "invalid_token", "message": "Token is invalid or revoked"}}, status=401)
    return principal, None


def authorize(request: HttpRequest, *, administrator: bool = False, scope: str = "employees") -> tuple[dict | None, JsonResponse | None]:
    principal, error = verified_principal(request)
    if error:
        return None, error

    scopes = set(principal.get("scopes") or [])
    roles = set(principal.get("roles") or [])
    is_user = principal.get("type") == "user"
    is_service = principal.get("type") == "service"

    if administrator:
        if not is_user or "administrator" not in roles:
            return None, JsonResponse({"detail": {"code": "administrator_required", "message": "Administrator role is required"}}, status=403)
    elif not ((is_user and "administrator" in roles) or (is_service and scope in scopes)):
        return None, JsonResponse({"detail": {"code": "scope_required", "message": f"Required scope: {scope}"}}, status=403)
    return principal, None
