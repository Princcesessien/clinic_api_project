import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret")
DEBUG = os.environ.get("DEBUG") == "1"
ALLOWED_HOSTS = ["*"]
API_KEY = os.environ["API_KEY"]

INSTALLED_APPS = ["corsheaders", "clinic"]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "clinic.middleware.ApiKeyMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        env="DATABASE_URL", conn_max_age=60, conn_health_checks=True
    )
}

USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
APPEND_SLASH = False

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_HEADERS = ["content-type", "x-api-key", "idempotency-key"]
CORS_EXPOSE_HEADERS = ["Retry-After"]