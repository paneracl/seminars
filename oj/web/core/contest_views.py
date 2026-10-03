from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from . import scoreboard as scoreboard_module
from . import statements as statements_module
import json

from .forms import LANGUAGE_TEMPLATES, SubmitForm
from .judging import enqueue
from .models import Contest, ContestProblem, Participation, Submission
from .views import _sample_input_for


def _get_contest(slug, user) -> Contest:
    contest = get_object_or_404(Contest, slug=slug)
    if not contest.is_listed and not (user.is_authenticated and user.is_staff):
        # Unlisted contests are still reachable by direct link for people who
        # were given it; only the listing is hidden.
        if not contest.is_registered(user):
            raise Http404
    return contest


def contest_list(request):
    contests = Contest.objects.all()
    if not request.user.is_staff:
        contests = contests.filter(is_listed=True)

    now = timezone.now()
    running, upcoming, finished = [], [], []
    for contest in contests:
        if contest.has_ended:
            finished.append(contest)
        elif contest.has_started:
            running.append(contest)
        else:
            upcoming.append(contest)
    upcoming.sort(key=lambda c: c.start_time)

    registered = set()
    if request.user.is_authenticated:
        registered = set(Participation.objects
                         .filter(user=request.user)
                         .values_list("contest_id", flat=True))

    return render(request, "core/contest_list.html", {
        "running": running, "upcoming": upcoming, "finished": finished,
        "registered": registered, "now": now,
    })


def contest_detail(request, slug):
    contest = _get_contest(slug, request.user)
    problems = []
    if contest.can_see_problems(request.user):
        problems = list(ContestProblem.objects
                        .filter(contest=contest)
                        .select_related("problem"))

    best: dict[int, float] = {}
    if request.user.is_authenticated and problems:
        for submission in Submission.objects.filter(
                contest=contest, user=request.user,
                status=Submission.Status.DONE):
            current = best.get(submission.problem_id, 0.0)
            best[submission.problem_id] = max(current, submission.score)

    for contest_problem in problems:
        contest_problem.best = best.get(contest_problem.problem_id)

    return render(request, "core/contest_detail.html", {
        "contest": contest,
        "problems": problems,
        "registered": contest.is_registered(request.user),
    })


@login_required
def contest_register(request, slug):
    contest = _get_contest(slug, request.user)
    if request.method != "POST":
        return redirect(contest)
    if contest.has_ended:
        messages.error(request, "This contest has finished.")
    elif not contest.open_registration and not request.user.is_staff:
        messages.error(request, "Registration for this contest is by invitation. "
                                "Ask your teacher to add you.")
    else:
        _, created = Participation.objects.get_or_create(
            contest=contest, user=request.user,
            defaults={"is_hidden": request.user.is_staff})
        messages.success(request, "You're registered." if created
                         else "You were already registered.")
    return redirect(contest)


def contest_problem(request, slug, label):
    contest = _get_contest(slug, request.user)
    contest_problem = get_object_or_404(
        ContestProblem.objects.select_related("problem"),
        contest=contest, label=label.upper())

    if not contest.can_see_problems(request.user):
        return render(request, "core/contest_sealed.html", {"contest": contest})

    recent = []
    if request.user.is_authenticated:
        recent = Submission.objects.filter(
            contest=contest, user=request.user,
            problem=contest_problem.problem)[:10]

    statement, all_statements = statements_module.pick(contest_problem.problem, request)
    return render(request, "core/contest_problem.html", {
        "contest": contest,
        "cp": contest_problem,
        "problem": contest_problem.problem,
        "form": SubmitForm(),
        "recent": recent,
        "can_submit": contest.can_submit(request.user),
        "statement": statement,
        "all_statements": all_statements,
        "language_templates_json": json.dumps(LANGUAGE_TEMPLATES),
        "sample_input": _sample_input_for(contest_problem.problem),
    })


def contest_statement_pdf(request, slug, label, language):
    """
    Contest statement PDF, behind the same seal as the problem page.

    This is the reason PDFs are not served from /static/: there, the file
    would be readable by anyone who guessed the path, before the contest
    opened.
    """
    contest = _get_contest(slug, request.user)
    contest_problem = get_object_or_404(
        ContestProblem.objects.select_related("problem"),
        contest=contest, label=label.upper())
    if not contest.can_see_problems(request.user):
        raise Http404
    return statements_module.serve_pdf(contest_problem.problem, language, request)


@login_required
def contest_submit(request, slug, label):
    contest = _get_contest(slug, request.user)
    contest_problem = get_object_or_404(
        ContestProblem.objects.select_related("problem"),
        contest=contest, label=label.upper())

    if request.method != "POST":
        return redirect("contest_problem", slug=contest.slug, label=contest_problem.label)

    if not contest.can_submit(request.user):
        if not contest.has_started:
            messages.error(request, "The contest hasn't started yet.")
        elif contest.is_running and not contest.is_registered(request.user):
            messages.error(request, "Register for the contest before submitting.")
        else:
            messages.error(request, "This contest is closed to submissions.")
        return redirect("contest_problem", slug=contest.slug, label=contest_problem.label)

    form = SubmitForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "core/contest_problem.html", {
            "contest": contest, "cp": contest_problem,
            "problem": contest_problem.problem, "form": form,
            "recent": Submission.objects.filter(
                contest=contest, user=request.user,
                problem=contest_problem.problem)[:10],
            "can_submit": True,
            "language_templates_json": json.dumps(LANGUAGE_TEMPLATES),
            "sample_input": _sample_input_for(contest_problem.problem),
        })

    cooldown = settings.SUBMIT_COOLDOWN_SECONDS
    if cooldown and not request.user.is_staff:
        since = timezone.now() - timedelta(seconds=cooldown)
        if Submission.objects.filter(user=request.user, created_at__gte=since).exists():
            messages.error(request, f"Please wait {cooldown} seconds between submissions.")
            return redirect("contest_problem", slug=contest.slug,
                            label=contest_problem.label)

    cap = settings.SUBMIT_HOURLY_CAP
    if cap and not request.user.is_staff:
        hour_ago = timezone.now() - timedelta(hours=1)
        if Submission.objects.filter(user=request.user,
                                     created_at__gte=hour_ago).count() >= cap:
            messages.error(request,
                           f"You've reached the limit of {cap} submissions per hour.")
            return redirect("contest_problem", slug=contest.slug,
                            label=contest_problem.label)

    if not contest_problem.problem.package_exists:
        messages.error(request, "This problem has no test data installed.")
        return redirect("contest_problem", slug=contest.slug, label=contest_problem.label)

    now = timezone.now()
    submission = Submission.objects.create(
        user=request.user,
        problem=contest_problem.problem,
        contest=contest,
        counts_for_ranking=(contest.counts_for_ranking(now)
                            and contest.is_registered(request.user)),
        language=form.cleaned_data["language"],
        source=form.cleaned_data["source"],
        source_name=form.cleaned_data.get("source_name", ""),
        feedback_mode="full",
    )
    enqueue(submission)
    return redirect(submission)


def contest_scoreboard(request, slug):
    contest = _get_contest(slug, request.user)
    if not contest.has_started and not request.user.is_staff:
        return render(request, "core/contest_sealed.html", {"contest": contest})
    context = scoreboard_module.build(contest, viewer=request.user)
    return render(request, "core/contest_scoreboard.html", context)


@login_required
def contest_run_code(request, slug, label):
    """Run Code inside a contest: same as the standalone endpoint, but gated
    by the contest seal so a problem cannot be run before it opens."""
    from . import views as base_views

    contest = _get_contest(slug, request.user)
    contest_problem = get_object_or_404(
        ContestProblem.objects.select_related("problem"),
        contest=contest, label=label.upper())
    if not contest.can_see_problems(request.user):
        raise Http404
    # Access is already decided by the contest seal above; call the shared
    # runner directly rather than run_code, which would re-check is_public and
    # wrongly reject a sealed contest problem.
    return base_views._run_code_for(request, contest_problem.problem)


def contest_clock(request, slug):
    """JSON clock so the contest pages agree with the server, not the laptop."""
    contest = _get_contest(slug, request.user)
    return JsonResponse({
        "now": timezone.now().isoformat(),
        "start": contest.start_time.isoformat(),
        "end": contest.end_time.isoformat(),
        "remaining": contest.seconds_remaining,
        "status": contest.status_label,
        "frozen": contest.is_frozen,
    })
