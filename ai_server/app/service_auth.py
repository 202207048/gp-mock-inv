"""Fail-closed authentication for every route except the minimal health check."""
from secrets import compare_digest

from fastapi import Request
from fastapi.responses import JSONResponse

from .settings import get_ai_service_token


def authenticate_service(request: Request) -> JSONResponse | None:
    if request.method == "GET" and request.url.path == "/health":
        return None
    expected = get_ai_service_token()
    if not expected or not expected.strip():
        return JSONResponse(status_code=503, content={"detail": "AI service unavailable"})
    values = request.headers.getlist("Authorization")
    parts = values[0].split(" ") if len(values) == 1 else []
    if (len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]
            or any(c.isspace() for c in parts[1])
            or not compare_digest(parts[1].encode("utf-8"), expected.encode("utf-8"))):
        return JSONResponse(status_code=401, content={"detail": "Service authentication required"},
                            headers={"WWW-Authenticate": "Bearer"})
    return None
