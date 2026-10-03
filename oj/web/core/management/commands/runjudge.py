"""
The judge worker.

    python3 manage.py runjudge --box-id 0

Run one of these per reserved CPU core, each with a distinct --box-id. In
production they are systemd units; locally you can just open a second
terminal, or skip the worker entirely and set OJ_JUDGE_INLINE=1.
"""

from __future__ import annotations

import signal
import time

from django.core.management.base import BaseCommand

from core.judging import claim_one, judge_submission, requeue_stale


class Command(BaseCommand):
    help = "Grade queued submissions until stopped."

    def add_arguments(self, parser):
        parser.add_argument("--box-id", type=int, default=0,
                            help="isolate sandbox id; must be unique per worker")
        parser.add_argument("--poll", type=float, default=1.0,
                            help="seconds to sleep when the queue is empty")
        parser.add_argument("--once", action="store_true",
                            help="grade at most one submission, then exit")

    def handle(self, *args, **options):
        box_id = options["box_id"]
        stopping = {"now": False}

        def stop(_signum, _frame):
            # Finish the submission in hand, then exit. Killing mid-run would
            # leave the row claimed and the student waiting for the stale
            # sweep to notice.
            stopping["now"] = True
            self.stdout.write(self.style.WARNING(
                "\nstop requested; finishing current submission"))

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)

        self.stdout.write(f"worker started (box {box_id})")
        recovered = requeue_stale()
        if recovered:
            self.stdout.write(self.style.WARNING(
                f"returned {recovered} abandoned submission(s) to the queue"))

        idle_since = time.monotonic()
        while not stopping["now"]:
            submission = claim_one(box_id)
            if submission is None:
                if options["once"]:
                    self.stdout.write("queue empty")
                    return
                if time.monotonic() - idle_since > 300:
                    requeue_stale()
                    idle_since = time.monotonic()
                time.sleep(options["poll"])
                continue

            started = time.monotonic()
            judge_submission(submission, box_id=box_id)
            elapsed = time.monotonic() - started
            style = self.style.SUCCESS if submission.verdict == "AC" else self.style.NOTICE
            self.stdout.write(style(
                f"#{submission.pk} {submission.problem.code} "
                f"{submission.user.username} -> {submission.verdict} "
                f"{submission.score:g}/{submission.max_score:g} ({elapsed:.1f}s)"))
            idle_since = time.monotonic()

            if options["once"]:
                return

        self.stdout.write("worker stopped")
