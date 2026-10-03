"""
The bridge between Django and the judging core.

The queue is the database. That is a deliberate choice over Redis/Celery: at
150 students the throughput is trivial, and every dependency you don't have is
a dependency that can't break the week of a contest. `claim_one` uses
SELECT ... FOR UPDATE SKIP LOCKED on PostgreSQL so several workers can drain
the queue without stepping on each other.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from judge.grader import Feedback, Problem as JudgeProblem, judge as run_judge, run_once

from .models import RunJob, Submission, SubmissionSubtask, SubmissionTest

logger = logging.getLogger(__name__)

# A submission claimed longer ago than this is assumed to belong to a worker
# that died, and is returned to the queue.
STALE_CLAIM = timedelta(minutes=15)


def claim_one(worker_box: int = 0) -> Submission | None:
    """Atomically take the oldest queued submission, or None."""
    with transaction.atomic():
        queryset = (Submission.objects
                    .select_for_update(skip_locked=True)
                    .filter(status=Submission.Status.PENDING)
                    .order_by("created_at"))
        submission = queryset.first()
        if submission is None:
            return None
        submission.status = Submission.Status.JUDGING
        submission.claimed_at = timezone.now()
        submission.save(update_fields=["status", "claimed_at"])
        return submission


def requeue_stale() -> int:
    """Return abandoned in-progress submissions to the queue."""
    cutoff = timezone.now() - STALE_CLAIM
    prune_run_jobs()
    return (Submission.objects
            .filter(status=Submission.Status.JUDGING, claimed_at__lt=cutoff)
            .update(status=Submission.Status.PENDING, claimed_at=None))


# --- Run Code ---------------------------------------------------------------

# A browser gives up waiting long before this; anything older is garbage.
RUN_JOB_TTL = timedelta(minutes=10)


def claim_run_job() -> RunJob | None:
    """Atomically take the oldest queued Run Code request, or None."""
    with transaction.atomic():
        job = (RunJob.objects
               .select_for_update(skip_locked=True)
               .filter(status=RunJob.Status.PENDING)
               .order_by("created_at")
               .first())
        if job is None:
            return None
        job.status = RunJob.Status.RUNNING
        job.claimed_at = timezone.now()
        job.save(update_fields=["status", "claimed_at"])
        return job


def execute_run_job(job: RunJob, *, box_id: int = 0) -> RunJob:
    """Compile and run one Run Code request and store the result. Never raises."""
    try:
        if not job.problem.package_exists:
            raise FileNotFoundError("This problem has no test data installed.")
        result = run_once(JudgeProblem(job.problem.package_dir), job.source,
                          job.language, job.stdin, box_id=box_id).to_dict()
    except Exception as exc:                       # noqa: BLE001
        logger.exception("run job %s failed", job.pk)
        result = {"status": "error", "message": f"{type(exc).__name__}: {exc}"}
    job.result = result
    job.status = RunJob.Status.DONE
    job.save(update_fields=["result", "status"])
    return job


def enqueue_run(job: RunJob) -> None:
    """Hand a Run Code request to the judge (or run it now, with JUDGE_INLINE)."""
    if settings.JUDGE_INLINE:
        execute_run_job(job)


def prune_run_jobs() -> int:
    deleted, _ = RunJob.objects.filter(
        created_at__lt=timezone.now() - RUN_JOB_TTL).delete()
    return deleted


def judge_submission(submission: Submission, *, box_id: int = 0) -> Submission:
    """Grade a submission and persist the result. Never raises."""
    submission.subtask_results.all().delete()

    try:
        if not submission.problem.package_exists:
            raise FileNotFoundError(
                f"problem package missing at {submission.problem.package_dir}")

        problem = JudgeProblem(submission.problem.package_dir)
        report = run_judge(
            problem, submission.source, submission.language,
            box_id=box_id, feedback=Feedback(submission.feedback_mode),
        )

        submission.verdict = report.verdict.value
        submission.score = report.score
        submission.max_score = report.max_score
        submission.cpu_time = report.cpu_time
        submission.memory_kb = report.memory_kb
        submission.compile_output = report.compile_output
        submission.message = report.message

        for subtask in report.subtasks:
            row = SubmissionSubtask.objects.create(
                submission=submission, index=subtask.index, points=subtask.points,
                awarded=subtask.awarded, verdict=subtask.verdict.value,
            )
            SubmissionTest.objects.bulk_create([
                SubmissionTest(
                    subtask=row, name=test.name, verdict=test.verdict.value,
                    score=test.score, cpu_time=test.cpu_time,
                    memory_kb=test.memory_kb, message=test.message[:500],
                )
                for test in subtask.tests
            ])

    except Exception as exc:                       # noqa: BLE001
        # A judge failure is our bug, not the student's. Record it plainly and
        # keep the queue moving rather than losing the submission.
        logger.exception("judging submission %s failed", submission.pk)
        submission.verdict = "IE"
        submission.score = 0.0
        submission.message = f"{type(exc).__name__}: {exc}"

    submission.status = Submission.Status.DONE
    submission.judged_at = timezone.now()
    submission.claimed_at = None
    submission.save()
    return submission


def enqueue(submission: Submission) -> None:
    """
    Hand a new submission to the judge.

    With JUDGE_INLINE the grading happens right here, inside the request. That
    is fine for one person testing locally and wrong for anything else: the
    request blocks for the full runtime of every test.
    """
    if settings.JUDGE_INLINE:
        submission.status = Submission.Status.JUDGING
        submission.save(update_fields=["status"])
        judge_submission(submission)
