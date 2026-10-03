#!/usr/bin/env bash
#
# Production rehearsal on your own machine.
#
# This runs the real stack -- DEBUG off, gunicorn instead of runserver, real
# static files, queue-based judging with separate workers -- so that the first
# time you meet a production-only problem is here and not on the server during
# a round. The only differences from the server are SQLite instead of
# PostgreSQL, the rlimit sandbox instead of isolate, and no nginx.
#
#   bash scripts/local-prod.sh          start everything
#   bash scripts/local-prod.sh stop     stop everything
#   bash scripts/local-prod.sh reset    delete the rehearsal database
#
# Ctrl-C stops it. Logs go to .local-prod/.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="$ROOT/.local-prod"
VENV="$ROOT/.venv"
WORKERS="${OJ_LOCAL_WORKERS:-2}"
PORT="${OJ_LOCAL_PORT:-8010}"

mkdir -p "$RUN"

stop_all() {
  for pidfile in "$RUN"/*.pid; do
    [ -e "$pidfile" ] || continue
    pid="$(cat "$pidfile")"
    if kill -0 "$pid" 2>/dev/null; then
      # SIGTERM, not SIGKILL: runjudge finishes the submission it is holding
      # before it exits, exactly as it will under systemd.
      kill -TERM "$pid" 2>/dev/null || true
      for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    fi
    rm -f "$pidfile"
  done
  echo "stopped."
}

case "${1:-start}" in
  stop)  stop_all; exit 0 ;;
  reset) stop_all; rm -f "$RUN/rehearsal.sqlite3"; echo "database removed."; exit 0 ;;
  start) ;;
  *) echo "usage: $0 [start|stop|reset]" >&2; exit 2 ;;
esac

if [ ! -x "$VENV/bin/python" ]; then
  echo "No virtualenv at $VENV — run these first:" >&2
  echo "  python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt" >&2
  exit 1
fi

if ! "$VENV/bin/python" -c "import gunicorn, whitenoise" 2>/dev/null; then
  echo "Installing gunicorn and whitenoise into the virtualenv…"
  "$VENV/bin/pip" install -q gunicorn whitenoise
fi

# A rehearsal-only key. The server gets its own in /etc/oj/oj.env, and this one
# must never travel there.
if [ ! -f "$RUN/secret" ]; then
  "$VENV/bin/python" -c "import secrets;print(secrets.token_urlsafe(64))" > "$RUN/secret"
  chmod 600 "$RUN/secret"
fi

export OJ_DEBUG=0
export OJ_SECRET_KEY="$(cat "$RUN/secret")"
export OJ_ALLOWED_HOSTS="localhost,127.0.0.1"
export OJ_CSRF_ORIGINS="http://localhost:$PORT,http://127.0.0.1:$PORT"
# No TLS locally, so the HTTPS redirect and secure cookies have to come off or
# nothing loads. On the server both stay on -- that is the point of nginx.
export OJ_SSL_REDIRECT=0
# No TLS here, so secure-only cookies would stop sign-in working entirely.
export OJ_SECURE_COOKIES=0
export OJ_HSTS_SECONDS=0
export OJ_DB=sqlite
export OJ_DB_PATH="$RUN/rehearsal.sqlite3"
export OJ_STATIC_ROOT="$RUN/static"
export OJ_PROBLEM_ROOT="$ROOT/problems"
export OJ_MEDIA_ROOT="$RUN/media"
export OJ_SANDBOX=rlimit
export OJ_JUDGE_INLINE=0
export OJ_BIND="127.0.0.1:$PORT"
export PYTHONUNBUFFERED=1

cd "$ROOT/web"

echo "→ migrating"
"$VENV/bin/python" manage.py migrate --no-input >/dev/null

echo "→ collecting static files"
"$VENV/bin/python" manage.py collectstatic --no-input >/dev/null

echo "→ deployment checks"
"$VENV/bin/python" manage.py check --deploy --fail-level ERROR

if ! "$VENV/bin/python" manage.py shell -c \
     "from core.models import Problem; import sys; sys.exit(0 if Problem.objects.exists() else 1)" 2>/dev/null; then
  echo "→ importing the sample problem"
  "$VENV/bin/python" manage.py importproblem ../problems/sumsub --public
fi

trap 'echo; stop_all; exit 0' INT TERM

echo "→ starting $WORKERS judge worker(s)"
for i in $(seq 0 $((WORKERS - 1))); do
  "$VENV/bin/python" manage.py runjudge --box-id "$i" \
      >> "$RUN/worker-$i.log" 2>&1 &
  echo $! > "$RUN/worker-$i.pid"
done

echo "→ starting gunicorn on http://127.0.0.1:$PORT/"
"$VENV/bin/gunicorn" -c "$ROOT/deploy/gunicorn.conf.py" ojsite.wsgi:application \
    >> "$RUN/web.log" 2>&1 &
echo $! > "$RUN/web.pid"

sleep 2
if ! kill -0 "$(cat "$RUN/web.pid")" 2>/dev/null; then
  echo "gunicorn failed to start. Last lines of $RUN/web.log:" >&2
  tail -20 "$RUN/web.log" >&2
  stop_all
  exit 1
fi

cat <<INFO

  Running at  http://127.0.0.1:$PORT/

  logs        $RUN/web.log
              $RUN/worker-0.log
  database    $RUN/rehearsal.sqlite3   (separate from your dev database)

  No superuser yet? In another terminal:
    cd $ROOT/web
    OJ_DB_PATH=$RUN/rehearsal.sqlite3 $VENV/bin/python manage.py createsuperuser

  Ctrl-C to stop.

INFO

wait
