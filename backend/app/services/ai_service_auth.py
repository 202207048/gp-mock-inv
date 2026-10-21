"""Service credentials stay on the Backend and are never copied from user headers."""
from app.config import settings


class AIServiceNotConfigured(Exception):
    def __init__(self):
        super().__init__("AI service unavailable")


def service_headers() -> dict[str, str]:
    token = settings.AI_SERVICE_TOKEN.get_secret_value()
    # Invalid header characters must fail before constructing an HTTP request.
    if not token or not token.isascii() or any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in token):
        raise AIServiceNotConfigured()
    return {"Authorization": "Bearer " + token}
