"""Gunicorn configuration.

Sync workers, not async: every request is short and CPU-light because the
actual grading happens in a separate process. Threads add nothing here and
make debugging harder.
"""

import multiprocessing
import os

bind = os.environ.get("OJ_BIND", "127.0.0.1:8010")

# Web workers are cheap; keep them off the cores reserved for judging.
workers = int(os.environ.get("OJ_WEB_WORKERS",
                            min(4, multiprocessing.cpu_count())))
worker_class = "sync"
timeout = 60
graceful_timeout = 30
keepalive = 5

# Recycle to bound any slow leak. Jitter avoids all workers restarting at once.
max_requests = 1000
max_requests_jitter = 100

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("OJ_LOG_LEVEL", "info")
# %({X-Forwarded-For}i)s gives the real client IP behind nginx.
access_log_format = '%({X-Forwarded-For}i)s %(m)s %(U)s %(s)s %(L)ss'
