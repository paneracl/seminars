"""
Data model.

Division of responsibility, deliberately:

  * The problem *package on disk* (problem.json + tests/) is the source of
    truth for judging: limits, subtasks, checker, test data. The judging core
    reads it directly and knows nothing about Django.
  * The database holds everything the *site* needs: statement, visibility,
    who submitted what, and a cached copy of the package metadata so problem
    lists render without touching the filesystem.

Importing a package refreshes the cached copy. If the two ever disagree, the
package wins, because that is what actually graded the submission.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils import timezone


class Problem(models.Model):
    code = models.SlugField(max_length=64, unique=True,
                            help_text="Directory name under PROBLEM_ROOT.")
    title = models.CharField(max_length=200)
    is_public = models.BooleanField(default=False,
                                    help_text="Visible to students outside contests.")

    # cached from problem.json at import time
    time_limit = models.FloatField(default=1.0)
    memory_limit_mb = models.IntegerField(default=256)
    max_score = models.FloatField(default=100.0)
    subtask_count = models.IntegerField(default=1)
    test_count = models.IntegerField(default=0)
    checker_type = models.CharField(max_length=32, default="token")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:
        return f"{self.code} — {self.title}"

    def get_absolute_url(self) -> str:
        return reverse("problem_detail", args=[self.code])

    @property
    def package_dir(self):
        return settings.PROBLEM_ROOT / self.code

    @property
    def package_exists(self) -> bool:
        return (self.package_dir / "problem.json").exists()

    def best_score_for(self, user) -> float | None:
        if not user.is_authenticated:
            return None
        row = self.submissions.filter(user=user, status=Submission.Status.DONE).aggregate(
            best=models.Max("score"))
        return row["best"]


class Contest(models.Model):
    class Scoring(models.TextChoices):
        IOI = "ioi", "IOI — subtask points, ranked by total score"
        ICPC = "icpc", "ICPC — solved count, ranked by penalty time"

    class Feedback(models.TextChoices):
        FULL = "full", "Full — every test shown"
        SUBTASK = "subtask", "Subtask scores only"
        FIRST_FAIL = "first", "Verdict and first failing test"
        MINIMAL = "minimal", "Verdict only"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=64, unique=True)
    description = models.TextField(blank=True, help_text="HTML. Greek is fine.")

    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    scoring = models.CharField(max_length=8, choices=Scoring.choices, default=Scoring.IOI)
    penalty_minutes = models.IntegerField(
        default=20, help_text="ICPC only: penalty added per rejected attempt "
                              "before an accepted one.")
    freeze_minutes = models.IntegerField(
        default=0, help_text="Freeze the public scoreboard for the last N minutes. "
                             "0 disables freezing.")

    feedback = models.CharField(max_length=16, choices=Feedback.choices,
                                default=Feedback.FULL)
    reveal_after_end = models.BooleanField(
        default=True, help_text="Show full per-test detail once the contest is over.")

    is_listed = models.BooleanField(default=False,
                                    help_text="Show on the contest list.")
    open_registration = models.BooleanField(
        default=True, help_text="Students may register themselves.")
    practice_after_end = models.BooleanField(
        default=True, help_text="Allow unranked submissions after the end time.")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_time"]

    def __str__(self) -> str:
        return self.name

    def get_absolute_url(self) -> str:
        return reverse("contest_detail", args=[self.slug])

    # --- state ---------------------------------------------------------

    @property
    def has_started(self) -> bool:
        return timezone.now() >= self.start_time

    @property
    def has_ended(self) -> bool:
        return timezone.now() >= self.end_time

    @property
    def is_running(self) -> bool:
        return self.has_started and not self.has_ended

    @property
    def freeze_at(self):
        if self.freeze_minutes <= 0:
            return None
        return self.end_time - timedelta(minutes=self.freeze_minutes)

    @property
    def is_frozen(self) -> bool:
        at = self.freeze_at
        return at is not None and timezone.now() >= at and not self.has_ended

    @property
    def seconds_remaining(self) -> int:
        return max(0, int((self.end_time - timezone.now()).total_seconds()))

    @property
    def status_label(self) -> str:
        if not self.has_started:
            return "Not started"
        if self.is_running:
            return "Frozen" if self.is_frozen else "Running"
        return "Finished"

    # --- access --------------------------------------------------------

    def is_registered(self, user) -> bool:
        if not user.is_authenticated:
            return False
        return self.participations.filter(user=user).exists()

    def can_see_problems(self, user) -> bool:
        """Problem statements stay sealed until the clock starts."""
        if user.is_authenticated and user.is_staff:
            return True
        if not self.has_started:
            return False
        if self.has_ended:
            return self.practice_after_end or self.is_listed
        return self.is_registered(user)

    def can_submit(self, user) -> bool:
        if not user.is_authenticated:
            return False
        if self.is_running:
            return self.is_registered(user) or user.is_staff
        if self.has_ended and self.practice_after_end:
            return True
        return user.is_staff and not self.has_started

    def counts_for_ranking(self, when) -> bool:
        return self.start_time <= when < self.end_time

    def detail_level_for(self, user) -> str:
        """How much per-test detail a viewer may see right now."""
        if user.is_authenticated and user.is_staff:
            return Contest.Feedback.FULL
        if self.has_ended and self.reveal_after_end:
            return Contest.Feedback.FULL
        return self.feedback


class ContestProblem(models.Model):
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE,
                                related_name="contest_problems")
    problem = models.ForeignKey(Problem, on_delete=models.PROTECT,
                                related_name="contest_problems")
    label = models.CharField(max_length=4, help_text="A, B, C…")
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "label"]
        constraints = [
            models.UniqueConstraint(fields=["contest", "problem"],
                                    name="unique_problem_per_contest"),
            models.UniqueConstraint(fields=["contest", "label"],
                                    name="unique_label_per_contest"),
        ]

    def __str__(self) -> str:
        return f"{self.contest.slug} {self.label}. {self.problem.title}"


class Participation(models.Model):
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE,
                                related_name="participations")
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name="participations")
    registered_at = models.DateTimeField(auto_now_add=True)
    is_hidden = models.BooleanField(
        default=False, help_text="Keep off the public scoreboard — testers, staff.")

    class Meta:
        ordering = ["registered_at"]
        constraints = [
            models.UniqueConstraint(fields=["contest", "user"],
                                    name="unique_participation"),
        ]

    def __str__(self) -> str:
        return f"{self.user.username} @ {self.contest.slug}"


def statement_upload_path(instance, filename):
    return f"statements/{instance.problem.code}/{instance.language}.pdf"


class Statement(models.Model):
    """
    One statement, in one language, for one problem.

    A statement can carry a Markdown/HTML body, a PDF, or both. When both are
    present the page shows the body and offers the PDF for download, which is
    the arrangement that suits a printed contest with online practice
    afterwards.

    PDFs are served through a view rather than from the static directory, so
    that a contest problem's statement cannot be fetched before the contest
    opens by anyone who guesses the URL.
    """

    class Language(models.TextChoices):
        EL = "el", "Ελληνικά"
        EN = "en", "English"

    class Format(models.TextChoices):
        MARKDOWN = "md", "Markdown"
        HTML = "html", "HTML"

    problem = models.ForeignKey(Problem, on_delete=models.CASCADE,
                                related_name="statements")
    language = models.CharField(max_length=5, choices=Language.choices,
                                default=Language.EL)
    title = models.CharField(max_length=200, blank=True,
                             help_text="Problem title in this language. "
                                       "Falls back to the problem title.")
    body_format = models.CharField(max_length=8, choices=Format.choices,
                                   default=Format.MARKDOWN)
    body = models.TextField(blank=True,
                            help_text="Maths: $inline$ and $$display$$.")
    pdf = models.FileField(upload_to=statement_upload_path, blank=True, null=True,
                           help_text="Optional. Shown alongside the text, or "
                                     "alone if there is no text.")
    is_default = models.BooleanField(
        default=False, help_text="Shown first when the reader has no preference.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_default", "language"]
        constraints = [
            models.UniqueConstraint(fields=["problem", "language"],
                                    name="unique_statement_language"),
        ]

    def __str__(self) -> str:
        return f"{self.problem.code} [{self.language}]"

    @property
    def has_body(self) -> bool:
        return bool(self.body.strip())

    @property
    def has_pdf(self) -> bool:
        return bool(self.pdf)

    def rendered(self) -> str:
        from .rendering import render_statement
        return render_statement(self.body, self.body_format)


class Submission(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Queued"
        JUDGING = "judging", "Judging"
        DONE = "done", "Judged"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submissions")
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="submissions")
    language = models.CharField(max_length=16)
    source = models.TextField()
    source_name = models.CharField(max_length=200, blank=True,
                                   help_text="Original filename, if uploaded.")

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    verdict = models.CharField(max_length=8, blank=True)
    score = models.FloatField(default=0.0)
    max_score = models.FloatField(default=0.0)
    cpu_time = models.FloatField(default=0.0)
    memory_kb = models.IntegerField(default=0)
    compile_output = models.TextField(blank=True)
    message = models.TextField(blank=True)

    contest = models.ForeignKey("Contest", null=True, blank=True,
                                on_delete=models.SET_NULL,
                                related_name="submissions")
    # True when submitted inside the contest window, so a late practice
    # submission can never quietly change a finished scoreboard.
    counts_for_ranking = models.BooleanField(default=False)

    # Judging always records full detail; how much of it a given viewer may
    # see is decided at display time by Contest.detail_level_for(). Storing
    # less would make post-contest reveal impossible without a full rejudge.
    feedback_mode = models.CharField(max_length=16, default="full")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    judged_at = models.DateTimeField(null=True, blank=True)
    # Set when a worker claims the row, so a crashed worker's claim can expire.
    claimed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["user", "problem"]),
            models.Index(fields=["contest", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.user} {self.problem.code} {self.verdict or self.status}"

    def get_absolute_url(self) -> str:
        return reverse("submission_detail", args=[self.pk])

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, self.verdict or self.get_status_display())

    @property
    def is_accepted(self) -> bool:
        return self.verdict == "AC"

    def visible_to(self, user) -> bool:
        return user.is_authenticated and (user == self.user or user.is_staff)


class SubmissionSubtask(models.Model):
    submission = models.ForeignKey(Submission, on_delete=models.CASCADE,
                                   related_name="subtask_results")
    index = models.IntegerField()
    points = models.FloatField()
    awarded = models.FloatField(default=0.0)
    verdict = models.CharField(max_length=8, blank=True)

    class Meta:
        ordering = ["index"]

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, self.verdict)


class SubmissionTest(models.Model):
    subtask = models.ForeignKey(SubmissionSubtask, on_delete=models.CASCADE,
                                related_name="test_results")
    name = models.CharField(max_length=64)
    verdict = models.CharField(max_length=8)
    score = models.FloatField(default=0.0)
    cpu_time = models.FloatField(default=0.0)
    memory_kb = models.IntegerField(default=0)
    message = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["id"]

    @property
    def verdict_label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, self.verdict)


VERDICT_LABELS = {
    "AC": "Accepted",
    "PA": "Partial",
    "WA": "Wrong Answer",
    "TLE": "Time Limit Exceeded",
    "MLE": "Memory Limit Exceeded",
    "RE": "Runtime Error",
    "CE": "Compilation Error",
    "OLE": "Output Limit Exceeded",
    "IE": "Judge Error",
    "SK": "Skipped",
}


class RunEvent(models.Model):
    """One Run Code invocation, kept only for rate limiting.

    A row per run is cheap and, unlike an in-process cache, is shared across
    gunicorn workers so the limit actually holds in production. Old rows are
    pruned opportunistically; nothing here is user-facing.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="run_events")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]


class RunJob(models.Model):
    """A Run Code request waiting for, or answered by, a judge worker.

    Run Code executes untrusted code, so it goes through the same workers as
    submissions instead of running inside the web process: the web tier stays
    unprivileged, each worker owns its own sandbox box (no two runs ever share
    one), and stopping oj-worker.target stops Run Code too. Rows are
    short-lived; the browser polls for the result and old rows are pruned.
    """
    class Status(models.TextChoices):
        PENDING = "pending", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="run_jobs")
    problem = models.ForeignKey(Problem, on_delete=models.CASCADE, related_name="run_jobs")
    language = models.CharField(max_length=16)
    source = models.TextField()
    stdin = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices,
                              default=Status.PENDING, db_index=True)
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
