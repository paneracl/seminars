"""
Settings for the school judge.

One settings file, switched by environment variables, so that the thing you
test locally is the thing that runs on the server. Defaults are the local
development values; production overrides them in the systemd unit or a .env.

    OJ_DEBUG=0                 -- turn off in production (default 1)
    OJ_SECRET_KEY=...          -- required when OJ_DEBUG=0
    OJ_ALLOWED_HOSTS=a,b       -- required when OJ_DEBUG=0
    OJ_DB=postgres             -- default 'sqlite'
    OJ_DB_NAME / OJ_DB_USER / OJ_DB_PASSWORD / OJ_DB_HOST / OJ_DB_PORT
    OJ_PROBLEM_ROOT=/srv/oj/problems
    OJ_JUDGE_INLINE=1          -- grade in-process on submit (dev convenience)
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent


def _flag(name: str, default: bool) -> bool:
    return os.environ.get(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


DEBUG = _flag("OJ_DEBUG", True)

SECRET_KEY = os.environ.get("OJ_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("OJ_SECRET_KEY must be set when OJ_DEBUG=0")
    SECRET_KEY = "dev-only-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = [h.strip() for h in os.environ.get("OJ_ALLOWED_HOSTS", "").split(",") if h.strip()]
if DEBUG and not ALLOWED_HOSTS:
    ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]", "testserver"]

CSRF_TRUSTED_ORIGINS = [
    o.strip() for o in os.environ.get("OJ_CSRF_ORIGINS", "").split(",") if o.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "core",
]

AUTHENTICATION_BACKENDS = [
    # AxesStandaloneBackend must come first: it locks out brute-force attempts
    # before the password is ever checked.
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise lets gunicorn serve static files on its own, so the whole
    # stack can be rehearsed locally without nginx. Optional: if it isn't
    # installed the line is dropped below and nginx handles statics.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
]

try:
    import whitenoise  # noqa: F401
except ImportError:
    MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": (
        "whitenoise.storage.CompressedManifestStaticFilesStorage"
        if any("whitenoise" in m for m in MIDDLEWARE)
        else "django.contrib.staticfiles.storage.StaticFilesStorage")},
}

ROOT_URLCONF = "ojsite.urls"
WSGI_APPLICATION = "ojsite.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]

if os.environ.get("OJ_DB", "sqlite") == "postgres":
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("OJ_DB_NAME", "oj"),
        "USER": os.environ.get("OJ_DB_USER", "oj"),
        "PASSWORD": os.environ.get("OJ_DB_PASSWORD", ""),
        "HOST": os.environ.get("OJ_DB_HOST", "127.0.0.1"),
        "PORT": os.environ.get("OJ_DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }}
else:
    DATABASES = {"default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("OJ_DB_PATH", BASE_DIR / "dev.sqlite3")),
        "OPTIONS": {"timeout": 20},
    }}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 8}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("OJ_TIME_ZONE", "Asia/Nicosia")
USE_I18N = True
USE_TZ = True

# Statement PDFs. Served through a view, never directly by nginx, so that
# contest access rules apply -- hence no MEDIA_URL.
MEDIA_ROOT = Path(os.environ.get("OJ_MEDIA_ROOT", REPO_ROOT / "media"))

STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ.get("OJ_STATIC_ROOT", BASE_DIR / "staticfiles"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "problem_list"
LOGOUT_REDIRECT_URL = "problem_list"

# --- judge-specific -------------------------------------------------------

# Where problem packages live on disk. The judging core reads these directly.
PROBLEM_ROOT = Path(os.environ.get("OJ_PROBLEM_ROOT", REPO_ROOT / "problems"))

# Grade synchronously inside the web request. Convenient for a single
# developer; unusable with real load because every submission blocks a worker
# thread for the full runtime of the tests. Production runs `runjudge`.
JUDGE_INLINE = _flag("OJ_JUDGE_INLINE", DEBUG)

# Which sandbox runs student code. "isolate" is the only safe choice for a
# real server. "rlimit" isolates nothing and exists for writing problems on a
# laptop, so with DEBUG off it is refused unless explicitly acknowledged
# (scripts/local-prod.sh does this for the local rehearsal only).
OJ_SANDBOX = os.environ.get("OJ_SANDBOX", "isolate")
if (not DEBUG and OJ_SANDBOX == "rlimit"
        and not _flag("OJ_ALLOW_UNSAFE_SANDBOX", False)):
    raise RuntimeError(
        "OJ_SANDBOX=rlimit runs student code with no isolation and is refused "
        "when OJ_DEBUG=0. Use isolate on a server; for a local rehearsal set "
        "OJ_ALLOW_UNSAFE_SANDBOX=1.")

# Submission source size cap, bytes.
MAX_SOURCE_BYTES = int(os.environ.get("OJ_MAX_SOURCE_BYTES", 128 * 1024))

# Seconds a user must wait between submissions.
SUBMIT_COOLDOWN_SECONDS = int(os.environ.get("OJ_SUBMIT_COOLDOWN", "10"))

# Hard cap on submissions per user per rolling hour. The cooldown stops
# fat-finger double-submits; this stops a script (or a frustrated student in a
# loop) from flooding the judge queue during a contest. Staff are exempt.
SUBMIT_HOURLY_CAP = int(os.environ.get("OJ_SUBMIT_HOURLY_CAP", "60"))

# Run Code (compile + run on custom input, ungraded). It runs untrusted code
# on demand -- through the judge workers, ahead of queued submissions -- so it
# gets its own tighter limits: a short per-minute burst limit and an hourly
# cap, both separate from submissions.
# Staff are exempt. Tighten RUN_PER_MINUTE first if the box ever feels the
# load during a contest.
RUN_PER_MINUTE = int(os.environ.get("OJ_RUN_PER_MINUTE", "6"))
RUN_HOURLY_CAP = int(os.environ.get("OJ_RUN_HOURLY_CAP", "80"))

# Largest custom input a student may paste into Run Code, bytes.
MAX_RUN_INPUT_BYTES = int(os.environ.get("OJ_MAX_RUN_INPUT_BYTES", 64 * 1024))

# --- login rate limiting (django-axes) --------------------------------
# Lock an account+IP after this many failures, for this long. Deliberately
# lenient on count and short on cooldown: the goal is to blunt brute force,
# not to lock out a student who mistypes a password three times before a
# contest. Lockout is per (username, IP), so one attacker cannot lock every
# student out by guessing their usernames.
AXES_FAILURE_LIMIT = int(os.environ.get("OJ_LOGIN_FAILURE_LIMIT", "8"))
AXES_COOLOFF_TIME = float(os.environ.get("OJ_LOGIN_COOLOFF_HOURS", "0.25"))  # 15 min
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ACCESS_FAILURE_LOG = True

import sys as _sys
if "test" in _sys.argv:
    AXES_ENABLED = False
    # Tests don't run collectstatic, so the hashed-manifest storage would fail
    # on the first {% static %} for a vendored asset. Plain storage sidesteps
    # that without affecting what production actually serves.
    STORAGES["staticfiles"]["BACKEND"] = \
        "django.contrib.staticfiles.storage.StaticFilesStorage"

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # Secure-only cookies are correct behind TLS and fatal without it: over
    # plain HTTP the browser will not send them back, so sign-in silently
    # fails. The local rehearsal turns this off because it has no TLS; on the
    # server it must stay on. Default is on, so forgetting is safe.
    _secure_cookies = _flag("OJ_SECURE_COOKIES", True)
    SESSION_COOKIE_SECURE = _secure_cookies
    CSRF_COOKIE_SECURE = _secure_cookies
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    X_FRAME_OPTIONS = "DENY"
    # nginx terminates TLS; these make Django refuse to serve plain HTTP.
    SECURE_SSL_REDIRECT = _flag("OJ_SSL_REDIRECT", True)
    # Start HSTS at one hour. Raise to 31536000 only once you are certain
    # every hostname on this domain will stay on HTTPS -- browsers honour the
    # old value until it expires, so a hasty large number is hard to undo.
    SECURE_HSTS_SECONDS = int(os.environ.get("OJ_HSTS_SECONDS", "3600"))


# Logging: judge workers write to the journal via stdout, so systemd captures
# them. Keep judging failures loud -- a silent IE is a student's problem that
# nobody hears about.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name}: {message}",
                             "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "WARNING",
                           "propagate": False},
        "core.judging": {"handlers": ["console"], "level": "INFO",
                         "propagate": False},
    },
}
