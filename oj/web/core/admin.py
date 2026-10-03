from __future__ import annotations

import json
import shutil
from pathlib import Path

from django.contrib import admin, messages
from django.forms import formset_factory
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse

from .forms import (ManualTestForm, ProblemAdminForm, ProblemPackageForm,
                    SubtaskForm)
from .judging import enqueue
from .models import (Contest, ContestProblem, Participation, Problem,
                     Statement, Submission, SubmissionSubtask, SubmissionTest)
from .packages import PackageError, import_package


class StatementInline(admin.StackedInline):
    model = Statement
    extra = 1
    fields = ("language", "is_default", "title", "body_format", "body", "pdf")
    classes = ["collapse"]


@admin.register(Problem)
class ProblemAdmin(admin.ModelAdmin):
    form = ProblemAdminForm
    inlines = [StatementInline]
    list_display = ("code", "title", "is_public", "time_limit", "memory_limit_mb",
                    "subtask_count", "test_count", "package_ok")
    list_filter = ("is_public", "checker_type")
    search_fields = ("code", "title")
    readonly_fields = ("max_score", "subtask_count", "test_count")
    fieldsets = (
        (None, {"fields": ("code", "title", "is_public")}),
        ("Judging settings", {
            "fields": ("time_limit", "memory_limit_mb", "checker_type",
                       "max_score", "subtask_count", "test_count"),
            "description": (
                "Time limit, memory and checker are editable here and are saved "
                "to problem.json. Max score, subtask count and test count are "
                "calculated automatically. Use 'Manage subtasks' to edit points "
                "and aggregation."
            ),
        }),
    )
    change_list_template = "admin/core/problem_changelist.html"
    change_form_template = "admin/core/problem_change_form.html"

    @admin.display(boolean=True, description="Test data")
    def package_ok(self, obj) -> bool:
        return obj.package_exists

    def get_urls(self):
        extra = [
            path("upload/", self.admin_site.admin_view(self.upload_view),
                 name="core_problem_upload"),
            path("<path:object_id>/tests/", self.admin_site.admin_view(self.tests_view),
                 name="core_problem_tests"),
            path("<path:object_id>/subtasks/", self.admin_site.admin_view(self.subtasks_view),
                 name="core_problem_subtasks"),
            path("<path:object_id>/statements/",
                 self.admin_site.admin_view(self.statements_view),
                 name="core_problem_statements"),
            path("statement-preview/",
                 self.admin_site.admin_view(self.statement_preview_view),
                 name="core_statement_preview"),
        ]
        return extra + super().get_urls()

    @staticmethod
    def _read_spec(problem: Problem) -> dict:
        spec_path = problem.package_dir / "problem.json"
        if not spec_path.exists():
            return {}
        try:
            return json.loads(spec_path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise PackageError(f"problem.json is not valid JSON: {exc}") from exc

    @staticmethod
    def _write_spec(problem: Problem, spec: dict) -> None:
        problem.package_dir.mkdir(parents=True, exist_ok=True)
        (problem.package_dir / spec.get("tests_dir", "tests")).mkdir(
            parents=True, exist_ok=True
        )
        (problem.package_dir / "problem.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    @staticmethod
    def _cached_stats(spec: dict) -> tuple[float, int, int]:
        subtasks = spec.get("subtasks") or []
        names = set()
        for subtask in subtasks:
            names.update(str(name) for name in subtask.get("tests", []))
        return (
            sum(float(s.get("points", 0)) for s in subtasks),
            len(subtasks),
            len(names),
        )

    def save_model(self, request, obj, form, change):
        """Keep editable admin settings and problem.json in sync."""
        old_code = None
        if change and obj.pk:
            try:
                old_code = Problem.objects.get(pk=obj.pk).code
            except Problem.DoesNotExist:
                pass

        # If the code changed, move the package before obj.package_dir starts
        # pointing at the new directory.
        if old_code and old_code != obj.code:
            from django.conf import settings
            old_dir = settings.PROBLEM_ROOT / old_code
            new_dir = settings.PROBLEM_ROOT / obj.code
            if old_dir.exists() and not new_dir.exists():
                new_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_dir), str(new_dir))

        super().save_model(request, obj, form, change)

        spec = self._read_spec(obj)
        if not spec:
            spec = {
                "code": obj.code,
                "title": obj.title,
                "time_limit": float(obj.time_limit),
                "memory_limit_mb": int(obj.memory_limit_mb),
                "checker": {"type": obj.checker_type or "token"},
                "subtasks": [
                    {"index": 1, "points": 100, "aggregation": "min", "tests": []}
                ],
            }

        spec["code"] = obj.code
        spec["title"] = obj.title
        spec["time_limit"] = float(obj.time_limit)
        spec["memory_limit_mb"] = int(obj.memory_limit_mb)

        previous_checker = spec.get("checker") or {"type": "token"}
        if previous_checker.get("type") == obj.checker_type:
            previous_checker["type"] = obj.checker_type
            spec["checker"] = previous_checker
        else:
            spec["checker"] = {"type": obj.checker_type}

        if not spec.get("subtasks"):
            spec["subtasks"] = [
                {"index": 1, "points": 100, "aggregation": "min", "tests": []}
            ]

        self._write_spec(obj, spec)
        max_score, subtask_count, test_count = self._cached_stats(spec)
        Problem.objects.filter(pk=obj.pk).update(
            max_score=max_score,
            subtask_count=subtask_count,
            test_count=test_count,
        )
        obj.max_score = max_score
        obj.subtask_count = subtask_count
        obj.test_count = test_count

    def response_add(self, request, obj, post_url_continue=None):
        if "_continue" not in request.POST and "_addanother" not in request.POST:
            self.message_user(
                request,
                "Problem created. You can now configure subtasks and add tests.",
                messages.INFO,
            )
            return HttpResponseRedirect(
                reverse("admin:core_problem_tests", args=[obj.pk])
            )
        return super().response_add(request, obj, post_url_continue)

    def _load_test_editor(self, problem: Problem):
        spec = self._read_spec(problem)
        if not spec:
            raise PackageError("problem.json is missing for this problem.")

        tests_dir = problem.package_dir / spec.get("tests_dir", "tests")
        tests_dir.mkdir(parents=True, exist_ok=True)
        subtasks = spec.get("subtasks") or [
            {"index": 1, "points": 100, "aggregation": "min", "tests": []}
        ]
        spec["subtasks"] = subtasks

        membership: dict[str, list[int]] = {}
        for subtask in subtasks:
            idx = int(subtask["index"])
            for name in subtask.get("tests", []):
                membership.setdefault(str(name), []).append(idx)

        names = sorted(
            {p.stem for p in tests_dir.glob("*.in")}
            | {p.stem for p in tests_dir.glob("*.ans")}
        )
        rows = []
        for name in names:
            input_path = tests_dir / f"{name}.in"
            answer_path = tests_dir / f"{name}.ans"
            rows.append({
                "original_name": name,
                "name": name,
                "input_text": input_path.read_text(encoding="utf-8-sig")
                if input_path.exists() else "",
                "answer_text": answer_path.read_text(encoding="utf-8-sig")
                if answer_path.exists() else "",
                "subtasks": [str(i) for i in membership.get(name, [])],
            })
        return spec, tests_dir, rows

    def tests_view(self, request, object_id):
        problem = get_object_or_404(Problem, pk=object_id)
        try:
            spec, tests_dir, initial = self._load_test_editor(problem)
        except PackageError as exc:
            messages.error(request, str(exc))
            return HttpResponseRedirect(
                reverse("admin:core_problem_change", args=[problem.pk])
            )

        TestFormSet = formset_factory(ManualTestForm, extra=1, can_delete=False)
        choices = [
            (int(s["index"]), f"{s['index']} — {s.get('points', 0):g} points ({s.get('aggregation', 'min')})")
            for s in spec["subtasks"]
        ]
        formset = TestFormSet(
            request.POST or None,
            initial=initial,
            prefix="tests",
            form_kwargs={"subtask_choices": choices},
        )
        subtask_indexes = [int(s["index"]) for s in spec["subtasks"]]

        if request.method == "POST" and formset.is_valid():
            rows = []
            names_seen: set[str] = set()
            has_error = False

            for form in formset:
                data = form.cleaned_data
                if not data.get("name") and not data.get("original_name"):
                    continue
                if data.get("DELETE"):
                    rows.append(data)
                    continue

                name = data.get("name")
                if name in names_seen:
                    form.add_error("name", "Each test name must be unique.")
                    has_error = True
                    continue
                names_seen.add(name)

                selected = data.get("subtasks") or []
                if not selected:
                    form.add_error("subtasks", "Assign the test to at least one subtask.")
                    has_error = True
                else:
                    bad = [idx for idx in selected if idx not in subtask_indexes]
                    if bad:
                        form.add_error("subtasks", "Unknown subtask selection.")
                        has_error = True
                rows.append(data)

            remaining = [r for r in rows if not r.get("DELETE")]
            if not remaining:
                messages.error(request, "A problem must contain at least one test.")
                has_error = True

            if not has_error:
                old_names = {r["original_name"] for r in rows if r.get("original_name")}
                new_names = {r["name"] for r in remaining}

                for row in remaining:
                    name = row["name"]
                    (tests_dir / f"{name}.in").write_text(
                        row.get("input_text") or "", encoding="utf-8"
                    )
                    (tests_dir / f"{name}.ans").write_text(
                        row.get("answer_text") or "", encoding="utf-8"
                    )

                for old_name in old_names - new_names:
                    (tests_dir / f"{old_name}.in").unlink(missing_ok=True)
                    (tests_dir / f"{old_name}.ans").unlink(missing_ok=True)

                memberships = {idx: [] for idx in subtask_indexes}
                for row in remaining:
                    for idx in row["subtasks"]:
                        memberships[idx].append(row["name"])
                for subtask in spec["subtasks"]:
                    subtask["tests"] = memberships[int(subtask["index"])]

                spec["code"] = problem.code
                spec["title"] = problem.title
                self._write_spec(problem, spec)

                try:
                    refreshed, warnings, notes = import_package(
                        problem.package_dir, code=problem.code, replace=True
                    )
                except PackageError as exc:
                    messages.error(request, f"Tests were not accepted: {exc}")
                else:
                    for note in notes:
                        messages.info(request, note)
                    for warning in warnings:
                        messages.warning(request, warning)
                    messages.success(
                        request,
                        f"Saved {refreshed.test_count} test(s) for {refreshed.code}.",
                    )
                    return HttpResponseRedirect(
                        reverse("admin:core_problem_tests", args=[problem.pk])
                    )

        return render(request, "admin/core/problem_tests.html", {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "problem": problem,
            "formset": formset,
            "subtasks": spec["subtasks"],
            "title": f"Manual tests — {problem.code}",
        })

    def subtasks_view(self, request, object_id):
        problem = get_object_or_404(Problem, pk=object_id)
        spec = self._read_spec(problem)
        if not spec:
            messages.error(request, "problem.json is missing for this problem.")
            return HttpResponseRedirect(
                reverse("admin:core_problem_change", args=[problem.pk])
            )

        subtasks = spec.get("subtasks") or [
            {"index": 1, "points": 100, "aggregation": "min", "tests": []}
        ]
        initial = [
            {
                "original_index": int(s["index"]),
                "index": int(s["index"]),
                "points": float(s.get("points", 0)),
                "aggregation": s.get("aggregation", "min"),
            }
            for s in subtasks
        ]

        SubtaskFormSet = formset_factory(SubtaskForm, extra=1, can_delete=False)
        formset = SubtaskFormSet(request.POST or None, initial=initial, prefix="subtasks")

        if request.method == "POST" and formset.is_valid():
            rows = []
            seen = set()
            has_error = False
            old_by_index = {int(s["index"]): s for s in subtasks}

            for form in formset:
                data = form.cleaned_data
                if not data.get("index") and not data.get("original_index"):
                    continue
                if data.get("DELETE"):
                    continue
                idx = int(data["index"])
                if idx in seen:
                    form.add_error("index", "Each subtask index must be unique.")
                    has_error = True
                    continue
                seen.add(idx)
                rows.append(data)

            if not rows:
                messages.error(request, "A problem must contain at least one subtask.")
                has_error = True

            if not has_error:
                new_subtasks = []
                for row in sorted(rows, key=lambda r: int(r["index"])):
                    old_idx = row.get("original_index")
                    old = old_by_index.get(int(old_idx)) if old_idx else None
                    new_subtasks.append({
                        "index": int(row["index"]),
                        "points": float(row["points"]),
                        "aggregation": row["aggregation"],
                        "tests": list(old.get("tests", [])) if old else [],
                    })

                spec["subtasks"] = new_subtasks
                spec["code"] = problem.code
                spec["title"] = problem.title
                self._write_spec(problem, spec)
                max_score, subtask_count, test_count = self._cached_stats(spec)
                Problem.objects.filter(pk=problem.pk).update(
                    max_score=max_score,
                    subtask_count=subtask_count,
                    test_count=test_count,
                )
                messages.success(
                    request,
                    f"Saved {subtask_count} subtask(s), total {max_score:g} points."
                )
                return HttpResponseRedirect(
                    reverse("admin:core_problem_subtasks", args=[problem.pk])
                )

        return render(request, "admin/core/problem_subtasks.html", {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "problem": problem,
            "formset": formset,
            "title": f"Manage subtasks — {problem.code}",
        })

    def statements_view(self, request, object_id):
        """Full-page statement editor: one card per language, toolbar + preview.

        Bodies are stored on the Statement model (not in the package), so this
        writes rows directly rather than round-tripping through the package.
        """
        from .models import Statement

        problem = get_object_or_404(Problem, pk=object_id)
        languages = [code for code, _ in Statement.Language.choices]

        if request.method == "POST":
            saved = 0
            default_lang = request.POST.get("default_language", "")
            for code in languages:
                body = request.POST.get(f"body_{code}", "").replace("\r\n", "\n")
                title = request.POST.get(f"title_{code}", "").strip()
                fmt = request.POST.get(f"format_{code}", Statement.Format.MARKDOWN)
                existing = Statement.objects.filter(problem=problem, language=code).first()

                # An empty body with no existing row and no PDF is simply skipped;
                # an emptied existing text body is treated as a deletion only if
                # there is also no PDF to keep the row alive for.
                if not body.strip():
                    if existing and not existing.pdf:
                        existing.delete()
                    elif existing:
                        existing.body = ""
                        existing.save(update_fields=["body"])
                    continue

                Statement.objects.update_or_create(
                    problem=problem, language=code,
                    defaults={
                        "body": body,
                        "title": title,
                        "body_format": fmt,
                        "is_default": (code == default_lang),
                    },
                )
                saved += 1

            # Guarantee exactly one default among whatever survived.
            rows = list(Statement.objects.filter(problem=problem))
            if rows and not any(r.is_default for r in rows):
                rows[0].is_default = True
                rows[0].save(update_fields=["is_default"])
            for row in rows:
                want = (row.language == default_lang)
                if row.is_default != want and any(r.language == default_lang for r in rows):
                    row.is_default = want
                    row.save(update_fields=["is_default"])

            messages.success(request, f"Saved {saved} statement(s).")
            return HttpResponseRedirect(
                reverse("admin:core_problem_statements", args=[problem.pk])
            )

        existing = {s.language: s for s in problem.statements.all()}
        cards = []
        for code, label in Statement.Language.choices:
            s = existing.get(code)
            cards.append({
                "code": code,
                "label": label,
                "title": s.title if s else "",
                "body": s.body if s else "",
                "body_format": s.body_format if s else Statement.Format.MARKDOWN,
                "is_default": s.is_default if s else (not existing and code == "el"),
                "has_pdf": bool(s and s.pdf),
            })

        return render(request, "admin/core/problem_statements.html", {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "problem": problem,
            "cards": cards,
            "format_choices": Statement.Format.choices,
            "preview_url": reverse("admin:core_statement_preview"),
            "title": f"Statements — {problem.code}",
        })

    def statement_preview_view(self, request):
        """Render Markdown/HTML to sanitised HTML for the live preview.

        Uses the exact renderer the public page uses, so what a teacher sees
        in preview is what a student will see — there is no second code path
        to drift out of sync.
        """
        from django.http import JsonResponse

        from .rendering import render_statement

        if request.method != "POST":
            return JsonResponse({"error": "POST required"}, status=405)
        body = request.POST.get("body", "")
        fmt = request.POST.get("format", "md")
        return JsonResponse({"html": render_statement(body, fmt)})

    def upload_view(self, request):
        form = ProblemPackageForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            upload = form.cleaned_data["archive"]
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                for chunk in upload.chunks():
                    tmp.write(chunk)
                temp_path = Path(tmp.name)
            try:
                problem, warnings, notes = import_package(
                    temp_path,
                    code=form.cleaned_data.get("code") or None,
                    replace=form.cleaned_data.get("replace", True),
                )
            except PackageError as exc:
                messages.error(request, f"Package rejected: {exc}")
            except Exception as exc:                      # noqa: BLE001
                messages.error(request, f"Import failed: {type(exc).__name__}: {exc}")
            else:
                for note in notes:
                    messages.info(request, note)
                for warning in warnings:
                    messages.warning(request, warning)
                messages.success(
                    request,
                    f"Imported {problem.code}: {problem.subtask_count} subtask(s), "
                    f"{problem.test_count} test(s), {problem.max_score:g} points. "
                    f"It is not visible to students until you tick 'is public'.")
                return HttpResponseRedirect(
                    reverse("admin:core_problem_change", args=[problem.pk]))
            finally:
                temp_path.unlink(missing_ok=True)

        return render(request, "admin/core/problem_upload.html", {
            **self.admin_site.each_context(request),
            "form": form,
            "title": "Upload problem package",
        })


class SubmissionSubtaskInline(admin.TabularInline):
    model = SubmissionSubtask
    extra = 0
    can_delete = False
    readonly_fields = ("index", "points", "awarded", "verdict")


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "user", "problem", "language",
                    "status", "verdict", "score_display")
    list_filter = ("status", "verdict", "language", "problem")
    search_fields = ("user__username", "problem__code")
    readonly_fields = ("user", "problem", "language", "source", "created_at",
                       "judged_at", "cpu_time", "memory_kb", "compile_output",
                       "message")
    inlines = [SubmissionSubtaskInline]
    actions = ["rejudge"]

    @admin.display(description="Score")
    def score_display(self, obj) -> str:
        return f"{obj.score:g}/{obj.max_score:g}" if obj.max_score else "—"

    @admin.action(description="Rejudge selected submissions")
    def rejudge(self, request, queryset):
        count = 0
        for submission in queryset:
            submission.status = Submission.Status.PENDING
            submission.verdict = ""
            submission.score = 0.0
            submission.claimed_at = None
            submission.save(update_fields=["status", "verdict", "score", "claimed_at"])
            enqueue(submission)
            count += 1
        self.message_user(request, f"{count} submission(s) queued for rejudging.",
                          messages.SUCCESS)


class ContestProblemInline(admin.TabularInline):
    model = ContestProblem
    extra = 3
    autocomplete_fields = ["problem"]


class ParticipationInline(admin.TabularInline):
    model = Participation
    extra = 0
    autocomplete_fields = ["user"]
    readonly_fields = ["registered_at"]


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "start_time", "end_time", "scoring",
                    "state", "is_listed", "entrants")
    list_filter = ("scoring", "is_listed")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ContestProblemInline, ParticipationInline]
    fieldsets = (
        (None, {"fields": ("name", "slug", "description")}),
        ("Window", {"fields": ("start_time", "end_time"),
                    "description": "Times are in the server timezone "
                                   "(TIME_ZONE in settings). Check it before a "
                                   "real round."}),
        ("Scoring", {"fields": ("scoring", "penalty_minutes", "freeze_minutes")}),
        ("What competitors see", {"fields": ("feedback", "reveal_after_end")}),
        ("Access", {"fields": ("is_listed", "open_registration",
                               "practice_after_end")}),
    )

    @admin.display(description="State")
    def state(self, obj) -> str:
        return obj.status_label

    @admin.display(description="Entrants")
    def entrants(self, obj) -> int:
        return obj.participations.count()


@admin.register(Participation)
class ParticipationAdmin(admin.ModelAdmin):
    list_display = ("contest", "user", "registered_at", "is_hidden")
    list_filter = ("contest", "is_hidden")
    search_fields = ("user__username", "contest__slug")
    autocomplete_fields = ["user", "contest"]
