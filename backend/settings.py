import os
import secrets
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from local_config import groq_api_key


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DJANGO_DATA_DIR", BASE_DIR / ".local"))
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("Set DJANGO_SECRET_KEY before deployment.")
    DATA_DIR.mkdir(exist_ok=True)
    secret_file = DATA_DIR / "secret.key"
    try:
        with secret_file.open("x") as handle:
            handle.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    SECRET_KEY = secret_file.read_text().strip()

ALLOWED_HOSTS = [host.strip() for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()]
BEHIND_PROXY = os.environ.get("DJANGO_BEHIND_PROXY", "0") == "1"
if not DEBUG:
    if not DATA_DIR.is_absolute():
        raise ImproperlyConfigured("DJANGO_DATA_DIR must be an absolute persistent path.")
    if not os.environ.get("DJANGO_ALLOWED_HOSTS") or not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
        raise ImproperlyConfigured("Set explicit DJANGO_ALLOWED_HOSTS before deployment.")
ROOT_URLCONF = "urls"
WSGI_APPLICATION = "wsgi.application"
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "django.contrib.sessions", "django.contrib.staticfiles", "login", "client_files", "agreements"]
MIDDLEWARE = [
    "proxy.TrustedProxyMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "login.middleware.RoleSessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
AUTH_USER_MODEL = "login.CompanyAccount"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

DB_ENGINE = os.environ.get("DJANGO_DB_ENGINE", "django.db.backends.sqlite3")
DB_NAME = os.environ.get("DJANGO_DB_NAME")
if not DEBUG and not DB_NAME:
    raise ImproperlyConfigured("Set DJANGO_DB_NAME to a persistent database before deployment.")
if DB_ENGINE == "django.db.backends.sqlite3":
    DATA_DIR.mkdir(exist_ok=True)
    DATABASES = {"default": {"ENGINE": DB_ENGINE, "NAME": DB_NAME or DATA_DIR / "db.sqlite3", "OPTIONS": {"timeout": 20}}}
else:
    DATABASES = {"default": {
        "ENGINE": DB_ENGINE, "NAME": DB_NAME,
        "USER": os.environ.get("DJANGO_DB_USER", ""),
        "PASSWORD": os.environ.get("DJANGO_DB_PASSWORD", ""),
        "HOST": os.environ.get("DJANGO_DB_HOST", ""),
        "PORT": os.environ.get("DJANGO_DB_PORT", ""),
    }}

TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [BASE_DIR.parent / "frontend"], "APP_DIRS": False}]
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR.parent / "frontend"]
STATIC_ROOT = Path(os.environ.get("DJANGO_STATIC_ROOT", DATA_DIR / "static"))
# Private documents are never served through Django's static/media URL handling.
AGREEMENT_STORAGE_ROOT = Path(os.environ.get("DJANGO_AGREEMENT_STORAGE_ROOT", DATA_DIR / "agreements"))
AGREEMENT_MAX_BYTES = 10 * 1024 * 1024
AGREEMENT_MAX_PAGES = 50
AGREEMENT_PROCESSING_TIMEOUT = 30
AGREEMENT_PROCESSING_MAX_MEMORY = 512 * 1024 * 1024
AGREEMENT_PREVIEW_MAX_EDGE = 1800
AGREEMENT_COMPLETION_FONT = os.environ.get("DJANGO_AGREEMENT_COMPLETION_FONT", "")
AGREEMENT_DOCKER_COMMAND = os.environ.get("DJANGO_AGREEMENT_DOCKER_COMMAND", "docker")
AGREEMENT_DOCX_IMAGE = os.environ.get("DJANGO_AGREEMENT_DOCX_IMAGE", "astra-docx:local")
AGREEMENT_DOCX_TIMEOUT = 55
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
DATA_UPLOAD_MAX_MEMORY_SIZE = 8192
SESSION_COOKIE_HTTPONLY = True
COMPANY_SESSION_COOKIE_NAME = "astra_company_session"
COMPANY_SESSION_COOKIE_PATH = "/company/"
CLIENT_SESSION_COOKIE_NAME = "astra_client_session"
CLIENT_SESSION_COOKIE_PATH = "/client/"
# Each role's session owns its CSRF secret, so rotation cannot affect the other.
CSRF_USE_SESSIONS = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
SECURE_SSL_REDIRECT = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True

CLIENT_CODE_TTL = 24 * 60 * 60
CLIENT_MAX_ATTEMPTS = 5
CLIENT_VERIFY_SCOPE_MAX_ATTEMPTS = 5
CLIENT_VERIFY_NETWORK_MAX_ATTEMPTS = 30
CLIENT_VERIFY_WINDOW = 15 * 60
CLIENT_GENERATION_MAX_CODES = 5
CLIENT_GENERATION_WINDOW = 60 * 60
CLIENT_GENERATION_COOLDOWN = 60
CLIENT_SESSION_IDLE = 30 * 60
CLIENT_SESSION_MAX = 2 * 60 * 60
COMPANY_LOGIN_MAX_ATTEMPTS = 5
COMPANY_LOGIN_WINDOW = 15 * 60

# Free-plan assistance is optional and never falls back to paid service.
ASSISTANT_GROQ_API_KEY = groq_api_key(BASE_DIR.parent / ".env")
ASSISTANT_MODEL = "openai/gpt-oss-20b"
ASSISTANT_QUESTION_MAX_CHARS = 2000
ASSISTANT_INPUT_MAX_BYTES = 14000
ASSISTANT_MAX_COMPLETION_TOKENS = 1000
ASSISTANT_TIMEOUT = 30
ASSISTANT_DAILY_CALLS = 25
# Below Groq's published Free Plan limits (30 RPM, 8K TPM, 200K TPD).
# Byte-based token estimates are conservative estimates, not an exact tokenizer.
ASSISTANT_REQUESTS_PER_MINUTE = 6
ASSISTANT_TOKENS_PER_MINUTE = 7200
