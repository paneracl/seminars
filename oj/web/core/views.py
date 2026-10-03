from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from . import statements as statements_module
import json

from .forms import LANGUAGE_TEMPLATES, RegisterForm, SubmitForm
from .judging import enqueue, enqueue_run
from .models import Problem, RunEvent, RunJob, Submission

from judge import Problem as JudgeProblem, languages


def problem_list(request):
    problems = Problem.objects.all()
    if not request.user.is_staff:
        problems = problems.filter(is_public=True)

    best: dict[int, float] = {}
    if request.user.is_authenticated:
        rows = (Submission.objects
                .filter(user=request.user, status=Submission.Status.DONE)
                .values("problem_id")
                .annotate(best=Max("score")))
        best = {row["problem_id"]: row["best"] for row in rows}

    problems = list(problems)
    for problem in problems:
        problem.best = best.get(problem.pk)

    return render(request, "core/problem_list.html", {"problems": problems})


def problem_detail(request, code):
    problem = get_object_or_404(Problem, code=code)
    if not problem.is_public and not request.user.is_staff:
        raise Http404
    recent = []
    if request.user.is_authenticated:
        recent = problem.submissions.filter(user=request.user)[:10]
    statement, all_statements = statements_module.pick(problem, request)
    return render(request, "core/problem_detail.html", {
        "problem": problem,
        "recent": recent,
        "form": SubmitForm(),
        "statement": statement,
        "all_statements": all_statements,
        "language_templates_json": json.dumps(LANGUAGE_TEMPLATES),
        "sample_input": _sample_input_for(problem),
    })


def problem_statement_pdf(request, code, language):
    """Public-problem PDF. Contest problems go through the contest view."""
    problem = get_object_or_404(Problem, code=code)
    if not problem.is_public and not request.user.is_staff:
        raise Http404
    return statements_module.serve_pdf(problem, language, request)


@login_required
def submit(request, code):
    problem = get_object_or_404(Problem, code=code)
    if not problem.is_public and not request.user.is_staff:
        raise Http404

    if request.method != "POST":
        return redirect(problem)

    form = SubmitForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "core/problem_detail.html", {
            "problem": problem, "form": form,
            "recent": problem.submissions.filter(user=request.user)[:10],
            "language_templates_json": json.dumps(LANGUAGE_TEMPLATES),
        })

    cooldown = settings.SUBMIT_COOLDOWN_SECONDS
    if cooldown and not request.user.is_staff:
        since = timezone.now() - timedelta(seconds=cooldown)
        if Submission.objects.filter(user=request.user, created_at__gte=since).exists():
            messages.error(request,
                           f"Please wait {cooldown} seconds between submissions.")
            return redirect(problem)

    cap = settings.SUBMIT_HOURLY_CAP
    if cap and not request.user.is_staff:
        hour_ago = timezone.now() - timedelta(hours=1)
        if Submission.objects.filter(user=request.user,
                                     created_at__gte=hour_ago).count() >= cap:
            messages.error(request,
                           f"You've reached the limit of {cap} submissions per hour. "
                           f"Take a moment, then try again.")
            return redirect(problem)

    if not problem.package_exists:
        messages.error(request, "This problem has no test data installed yet.")
        return redirect(problem)

    submission = Submission.objects.create(
        user=request.user,
        problem=problem,
        language=form.cleaned_data["language"],
        source=form.cleaned_data["source"],
        source_name=form.cleaned_data.get("source_name", ""),
        feedback_mode="full",
    )
    enqueue(submission)
    return redirect(submission)


@login_required
def submission_detail(request, pk):
    submission = get_object_or_404(
        Submission.objects.select_related("problem", "user", "contest"), pk=pk)
    if not submission.visible_to(request.user):
        raise Http404

    subtasks = list(submission.subtask_results.prefetch_related("test_results"))
    for subtask in subtasks:
        fraction = (subtask.awarded / subtask.points) if subtask.points else 0.0
        subtask.fill_pct = round(fraction * 100, 2)
        subtask.state = "full" if fraction >= 0.999 else ("zero" if fraction <= 0.001
                                                          else "partial")

    # Judging always stored full detail; how much of it this viewer may see is
    # decided here, so a contest can reveal everything after it finishes
    # without rejudging anything.
    level = "full"
    if submission.contest is not None:
        level = submission.contest.detail_level_for(request.user)

    hide_score = False
    for subtask in subtasks:
        tests = list(subtask.test_results.all())
        if level == "full":
            subtask.visible_tests = tests
        elif level == "subtask":
            subtask.visible_tests = []
        elif level == "first":
            shown = []
            for test in tests:
                shown.append(test)
                if test.verdict != "AC":
                    break
            subtask.visible_tests = shown
        else:
            subtask.visible_tests = []
            hide_score = True

    return render(request, "core/submission_detail.html", {
        "submission": submission,
        "subtasks": [] if level == "minimal" else subtasks,
        "detail_level": level,
        "hide_score": hide_score,
    })


@login_required
def submission_status(request, pk):
    """Small JSON endpoint so the result page can refresh itself while queued."""
    submission = get_object_or_404(Submission, pk=pk)
    if not submission.visible_to(request.user):
        raise Http404
    return JsonResponse({
        "status": submission.status,
        "verdict": submission.verdict,
        "score": submission.score,
        "max_score": submission.max_score,
    })


def _sample_input_for(problem):
    """Sample input to prefill Run Code, or "" if none can be safely shown.

    Respects the same hide_sample_input opt-out as the runner, so a problem
    with a secret first test never puts that input into the page.
    """
    if not problem.package_exists:
        return ""
    try:
        sample = JudgeProblem(problem.package_dir).sample_input()
    except Exception:                              # noqa: BLE001 - never break the page
        return ""
    return sample or ""


@login_required
def run_code(request, code):
    """Compile + run on custom input, ungraded (HackerRank-style "Run Code").

    Runs untrusted code on demand, so it enforces its own rate limits and
    never counts toward the submission cap. The run itself uses the same
    sandbox and limits as judging.
    """
    problem = get_object_or_404(Problem, code=code)
    if not problem.is_public and not request.user.is_staff:
        raise Http404
    return _run_code_for(request, problem)


def _run_code_for(request, problem):
    """Shared Run Code implementation. The caller has already decided access."""
    if request.method != "POST":
        return JsonResponse({"status": "error", "message": "POST required"}, status=405)
    if not problem.package_exists:
        return JsonResponse({"status": "error",
                             "message": "This problem has no test data installed."})

    language = request.POST.get("language", languages.DEFAULT_LANGUAGE)
    if language not in languages.LANGUAGES:
        return JsonResponse({"status": "error", "message": "Unknown language."})

    source = (request.POST.get("source") or "").replace("\r\n", "\n")
    if not source.strip():
        return JsonResponse({"status": "error", "message": "There's no code to run."})
    if len(source.encode("utf-8")) > settings.MAX_SOURCE_BYTES:
        return JsonResponse({"status": "error", "message": "Your code is too large."})

    stdin_text = (request.POST.get("stdin") or "").replace("\r\n", "\n")
    # Empty box -> run against the problem's sample input, so a student who
    # just hits Run sees a meaningful result instead of feeding the program
    # nothing. The student can always type their own input to override.
    if not stdin_text.strip():
        judge_problem_for_sample = JudgeProblem(problem.package_dir)
        sample = judge_problem_for_sample.sample_input()
        if sample:
            stdin_text = sample.replace("\r\n", "\n")
    if len(stdin_text.encode("utf-8")) > settings.MAX_RUN_INPUT_BYTES:
        limit_kb = settings.MAX_RUN_INPUT_BYTES // 1024
        return JsonResponse({"status": "error",
                             "message": f"Your input is too large (limit {limit_kb} KB)."})

    # --- rate limiting (staff exempt) ------------------------------------
    if not request.user.is_staff:
        now = timezone.now()
        minute_ago = now - timedelta(minutes=1)
        hour_ago = now - timedelta(hours=1)
        recent = RunEvent.objects.filter(user=request.user, created_at__gte=hour_ago)
        hourly = recent.count()
        if hourly >= settings.RUN_HOURLY_CAP:
            return JsonResponse({"status": "rate_limited",
                                 "message": "You've reached the hourly Run limit. "
                                            "Submit your solution, or try again later."})
        per_minute = recent.filter(created_at__gte=minute_ago).count()
        if per_minute >= settings.RUN_PER_MINUTE:
            return JsonResponse({"status": "rate_limited",
                                 "message": "You're running very frequently — "
                                            "wait a few seconds and try again."})

    RunEvent.objects.create(user=request.user)
    if not request.user.is_staff:
        RunEvent.objects.filter(
            user=request.user,
            created_at__lt=timezone.now() - timedelta(hours=2)).delete()

    # The run itself happens in a judge worker, never in the web process.
    job = RunJob.objects.create(user=request.user, problem=problem,
                                language=language, source=source, stdin=stdin_text)
    enqueue_run(job)
    return _run_job_response(request, job)


def _run_job_response(request, job):
    if job.status == RunJob.Status.DONE and job.result is not None:
        return JsonResponse(job.result)
    return JsonResponse({"status": "queued",
                         "poll_url": reverse("run_status", args=[job.pk])})


@login_required
def run_status(request, pk):
    """Poll endpoint for a queued Run Code request. Owner only."""
    job = get_object_or_404(RunJob, pk=pk, user=request.user)
    return _run_job_response(request, job)


@login_required
def submission_list(request):
    queryset = Submission.objects.select_related("problem", "user")
    if not request.user.is_staff:
        queryset = queryset.filter(user=request.user)
    page = Paginator(queryset, 50).get_page(request.GET.get("page"))
    return render(request, "core/submission_list.html", {"page": page})


def register(request):
    if request.user.is_authenticated:
        return redirect("problem_list")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        # Two backends are configured (axes + ModelBackend), so Django needs
        # to be told which one vouches for a user who never typed a password
        # into the login form; without this, every sign-up is a 500.
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Welcome. Your account is ready.")
        return redirect("problem_list")
    return render(request, "registration/register.html", {"form": form})
