import hmac

from django.conf import settings
from django.http import JsonResponse


class ApiKeyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        key = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(key.encode(), settings.API_KEY.encode()):
            return JsonResponse({"error": "invalid or missing API key"}, status=401)
        return self.get_response(request)