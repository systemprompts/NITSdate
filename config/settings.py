"""Django settings for nitsdate.

Everything environment-specific comes from environment variables (loaded from
.env). Nothing is kept in process memory between requests. See .env.example
for every setting and README.md for deployment.
"""
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(int(default))).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DEBUG")
if not DEBUG and (SECRET_KEY.startswith("dev-insecure") or len(SECRET_KEY) < 40):
    raise ImproperlyConfigured("Set a long random SECRET_KEY in .env before running with DEBUG=0.")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "accounts",
    "feed",
    "chat",
    "matchmaking",
    "moderation",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "accounts.middleware.OnboardingMiddleware",
    "accounts.middleware.LastSeenMiddleware",
]

if DEBUG:  # runserver serves static files itself; WhiteNoise is for production
    MIDDLEWARE.remove("whitenoise.middleware.WhiteNoiseMiddleware")

# allauth's EmailAddress has a conditional unique constraint MySQL can't express;
# harmless here because ACCOUNT_UNIQUE_EMAIL is enforced in code.
SILENCED_SYSTEM_CHECKS = ["models.W036"]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "accounts.context_processors.nav",
            ],
            "builtins": ["accounts.templatetags.nits", "django.templatetags.static", "django.contrib.humanize.templatetags.humanize"],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.environ.get("DB_NAME", "nitsdate"),
        "USER": os.environ.get("DB_USER", "root"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        "CONN_MAX_AGE": env_int("DB_CONN_MAX_AGE", 0),
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"  # "10 requests per day" resets at midnight IST
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Uploaded photos. Every photo is served through a permission-checking view,
# never from a public URL, so the bucket stays private.
#   MEDIA_STORAGE=local -> files under MEDIA_ROOT (default)
#   MEDIA_STORAGE=s3    -> any S3-compatible bucket: Cloudflare R2, Oracle Object Storage, AWS ...
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))
MEDIA_URL = "/media/"
MEDIA_STORAGE = os.environ.get("MEDIA_STORAGE", "local").strip().lower()
if MEDIA_STORAGE == "s3":
    from botocore.config import Config as _BotoConfig

    _default_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": os.environ["S3_BUCKET"],
            "endpoint_url": os.environ["S3_ENDPOINT_URL"],
            "access_key": os.environ["S3_ACCESS_KEY_ID"],
            "secret_key": os.environ["S3_SECRET_ACCESS_KEY"],
            "region_name": os.environ.get("S3_REGION", "auto"),
            "location": os.environ.get("S3_PREFIX", "media"),
            "default_acl": None,          # private objects; R2 doesn't support ACLs
            "querystring_auth": True,
            "file_overwrite": False,
            "client_config": _BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": os.environ.get("S3_ADDRESSING_STYLE", "path")},
                # newer boto3 adds checksums some S3-compatible stores reject
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        },
    }
elif MEDIA_STORAGE == "local":
    _default_storage = {"BACKEND": "django.core.files.storage.FileSystemStorage"}
else:
    raise ImproperlyConfigured("MEDIA_STORAGE must be 'local' or 's3'.")
STORAGES = {
    "default": _default_storage,
    # hashed file names in production so browsers can cache CSS/JS for a long time
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
MAX_IMAGE_UPLOAD_MB = 8

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SITE_ID = 1

# The Django admin lives at a non-obvious path (set ADMIN_URL in .env).
ADMIN_URL = os.environ.get("ADMIN_URL", "admin/").strip("/") + "/"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "login"

# ---- allauth / Google sign-in -------------------------------------------
ACCOUNT_ADAPTER = "accounts.adapters.AccountAdapter"
SOCIALACCOUNT_ADAPTER = "accounts.adapters.SocialAccountAdapter"
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_UNIQUE_EMAIL = True
SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION = True
SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True
SOCIALACCOUNT_QUERY_EMAIL = True
SOCIALACCOUNT_STORE_TOKENS = False
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online", "prompt": "select_account"},
        "OAUTH_PKCE_ENABLED": True,
    }
}

# Empty = every Google account may sign in. "nits.ac.in" = only *@nits.ac.in
# and *@<dept>.nits.ac.in.
# shown on /privacy/ and /terms/ for account-deletion requests (leave empty to hide)
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "").strip()
ALLOWED_EMAIL_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "").strip().lower().lstrip("@.")

# Password-less test login for trying the site with many users locally.
DEV_LOGIN = env_bool("DEV_LOGIN", DEBUG)

# ---- product rules (configuration, not runtime state) ------------------
PROFILE_REQUESTS_PER_DAY = env_int("PROFILE_REQUESTS_PER_DAY", 10)
MATCH_CONNECT_MESSAGES = env_int("MATCH_CONNECT_MESSAGES", 10)   # each side, in the match chat
PRIORITY_MESSAGES = env_int("PRIORITY_MESSAGES", 100)            # each side, whole conversation
MATCH_SEARCH_HOURS = env_int("MATCH_SEARCH_HOURS", 24)           # a search stays open this long
MATCH_COOLDOWN_DAYS = env_int("MATCH_COOLDOWN_DAYS", 3)          # same pair can't re-match within
MATCH_REVEAL_SECONDS = env_int("MATCH_REVEAL_SECONDS", 3)        # a new search shows "matching" at least this long
MATCH_ACTIVE_MINUTES = env_int("MATCH_ACTIVE_MINUTES", 20)       # searchers seen this recently are paired first
MIN_AGE = 16      # youngest allowed to join
ADULT_AGE = 18    # under this, people are only paired (matchmaking + connections) with each other
GALLERY_MAX_PHOTOS = env_int("GALLERY_MAX_PHOTOS", 10)

# Admins: Django superusers plus these emails (comma separated). Admins choose moderators in the app.
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
MOD_CONTEXT_SIZES = (10, 20, 100)

# ---- production hardening (DEBUG=0) ---------------------------------------
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")   # nginx terminates TLS
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]                          # local health checks over http
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = True        # the JS reads the token from a <meta> tag, not the cookie
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)       # raise once the final domain is settled
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    ACCOUNT_DEFAULT_HTTP_PROTOCOL = "https"
    if DEV_LOGIN:
        import warnings
        warnings.warn("DEV_LOGIN is on while DEBUG=0: anyone can log in as test accounts. Turn it off for real users.")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {"django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False}},
}
