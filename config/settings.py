"""
Django settings for the ISP Management Platform (Milestone 0.1 / 0.2).
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-key-change-me")

DEBUG = os.environ.get("DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = [
   "*"
]


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "customers",
    "plans",
    "subscribers",
    "network",
    "aaa",
    "accounts",
    "billing",
    "payments",
    "dashboard",
    "leads",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    "default": dj_database_url.parse(
        os.environ.get(
            "DATABASE_URL",
            f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        )
    )
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Subscriber imports are stored outside STATIC_ROOT and are only served through
# authenticated API views. Production storage should use an equally private backend.
MEDIA_ROOT = BASE_DIR / "private_media"
MEDIA_URL = "/private-media/"
SUBSCRIBER_IMPORT_MAX_BYTES = int(os.environ.get("SUBSCRIBER_IMPORT_MAX_BYTES", 10 * 1024 * 1024))

# Router management security. Generate the Fernet key once with:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
NAS_ENCRYPTION_KEY = os.environ.get("NAS_ENCRYPTION_KEY", "")
NAS_ALLOWED_NETWORKS = [item.strip() for item in os.environ.get("NAS_ALLOWED_NETWORKS", "").split(",") if item.strip()]
NAS_ALLOW_PRIVATE_NETWORKS = os.environ.get("NAS_ALLOW_PRIVATE_NETWORKS", "false").lower() == "true"
NAS_ALLOW_INSECURE_TLS = os.environ.get("NAS_ALLOW_INSECURE_TLS", "false").lower() == "true"
RADIUS_SERVER_IP = os.environ.get("RADIUS_SERVER_IP", "")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# CORS
# Mirrors the Node service's app.use(cors()) (isp-express-main/src/app.ts),
# which allows all origins with no restrictions.

CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_HEADERS = ["*"]


# Internal AAA <-> FreeRADIUS contract

INTERNAL_API_KEY = os.environ.get("INTERNAL_API_KEY", "change-me-too")


# Admin JWT auth

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-jwt-secret")
JWT_EXPIRES_IN = os.environ.get("JWT_EXPIRES_IN", "1d")
DEFAULT_ADMIN_EMAIL = os.environ.get("DEFAULT_ADMIN_EMAIL", "admin@example.com")
DEFAULT_ADMIN_PASSWORD = os.environ.get("DEFAULT_ADMIN_PASSWORD", "Admin@123")


# Django REST Framework

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "aaa.exceptions.internal_aaa_exception_handler",
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_RATES": {"nas_connection_test": "10/hour"},
}

SPECTACULAR_SETTINGS = {
    "TITLE": "ISP Management Platform API",
    "DESCRIPTION": "Admin business API (/api/v1) and internal AAA API (/internal/aaa) for the ISP Management Platform.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}
