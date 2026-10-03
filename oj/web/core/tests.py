"""
Scoreboard tests.

These check arithmetic, not rendering. Penalty maths is exactly the kind of
thing that looks right, ships, and is discovered to be wrong by a student who
came second.

    python3 manage.py test core
"""

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from core import scoreboard
from core.models import (Contest, ContestProblem, Participation, Problem,
                         Submission)


class ScoreboardTests(TestCase):
    def setUp(self):
        self.start = timezone.now() - timedelta(hours=3)
        self.end = self.start + timedelta(hours=5)      # still running
        self.alice = User.objects.create_user("alice")
        self.bob = User.objects.create_user("bob")
        self.problems = [
            Problem.objects.create(code=f"p{i}", title=f"Problem {i}",
                                   max_score=100, is_public=True)
            for i in (1, 2)
        ]

    def make_contest(self, scoring, **kwargs):
        contest = Contest.objects.create(
            name="Test", slug=f"t-{scoring}-{kwargs.get('freeze_minutes', 0)}",
            start_time=self.start, end_time=self.end,
            scoring=scoring, penalty_minutes=20, **kwargs)
        for index, problem in enumerate(self.problems):
            ContestProblem.objects.create(contest=contest, problem=problem,
                                          label="AB"[index], order=index)
        for user in (self.alice, self.bob):
            Participation.objects.create(contest=contest, user=user)
        return contest

    def submit(self, contest, user, problem, verdict, minutes, score=None):
        if score is None:
            score = 100.0 if verdict == "AC" else 0.0
        submission = Submission.objects.create(
            user=user, problem=problem, contest=contest, counts_for_ranking=True,
            language="cpp17", source="x", status=Submission.Status.DONE,
            verdict=verdict, score=score, max_score=100.0)
        Submission.objects.filter(pk=submission.pk).update(
            created_at=self.start + timedelta(minutes=minutes))
        return submission

    # --- ICPC ----------------------------------------------------------

    def test_icpc_penalty_counts_only_rejects_before_the_accept(self):
        contest = self.make_contest(Contest.Scoring.ICPC)
        # alice: two wrong, then accepted at minute 45 -> 45 + 2*20 = 85
        self.submit(contest, self.alice, self.problems[0], "WA", 10)
        self.submit(contest, self.alice, self.problems[0], "TLE", 20)
        self.submit(contest, self.alice, self.problems[0], "AC", 45)
        # a later submission on a solved problem must not add penalty
        self.submit(contest, self.alice, self.problems[0], "WA", 60)

        board = scoreboard.build(contest, viewer=self.alice)
        row = next(r for r in board["rows"] if r.user == self.alice)
        self.assertEqual(row.solved, 1)
        self.assertEqual(row.penalty, 85)
        self.assertEqual(row.cells["A"].attempts, 2)
        self.assertEqual(row.cells["A"].minutes, 45)

    def test_icpc_compile_errors_are_not_penalised(self):
        contest = self.make_contest(Contest.Scoring.ICPC)
        self.submit(contest, self.alice, self.problems[0], "CE", 5)
        self.submit(contest, self.alice, self.problems[0], "CE", 6)
        self.submit(contest, self.alice, self.problems[0], "AC", 30)

        row = next(r for r in scoreboard.build(contest)["rows"]
                   if r.user == self.alice)
        self.assertEqual(row.penalty, 30)

    def test_icpc_unsolved_attempts_cost_nothing(self):
        contest = self.make_contest(Contest.Scoring.ICPC)
        self.submit(contest, self.alice, self.problems[0], "AC", 10)
        self.submit(contest, self.alice, self.problems[1], "WA", 20)
        self.submit(contest, self.alice, self.problems[1], "WA", 30)

        row = next(r for r in scoreboard.build(contest)["rows"]
                   if r.user == self.alice)
        self.assertEqual(row.solved, 1)
        self.assertEqual(row.penalty, 10)

    def test_icpc_ranking_prefers_more_solved_then_less_penalty(self):
        contest = self.make_contest(Contest.Scoring.ICPC)
        # bob solves one problem fast; alice solves two slowly
        self.submit(contest, self.bob, self.problems[0], "AC", 5)
        self.submit(contest, self.alice, self.problems[0], "AC", 100)
        self.submit(contest, self.alice, self.problems[1], "AC", 150)

        rows = scoreboard.build(contest)["rows"]
        self.assertEqual(rows[0].user, self.alice)
        self.assertEqual(rows[0].rank, 1)
        self.assertEqual(rows[1].user, self.bob)
        self.assertEqual(rows[1].rank, 2)

    # --- IOI -----------------------------------------------------------

    def test_ioi_takes_the_best_score_per_problem(self):
        contest = self.make_contest(Contest.Scoring.IOI)
        self.submit(contest, self.alice, self.problems[0], "PA", 10, score=30)
        self.submit(contest, self.alice, self.problems[0], "PA", 20, score=70)
        # a later worse submission must not reduce the score
        self.submit(contest, self.alice, self.problems[0], "WA", 30, score=0)

        row = next(r for r in scoreboard.build(contest)["rows"]
                   if r.user == self.alice)
        self.assertEqual(row.total, 70)

    def test_ioi_ties_broken_by_who_got_there_first(self):
        contest = self.make_contest(Contest.Scoring.IOI)
        self.submit(contest, self.bob, self.problems[0], "AC", 200, score=100)
        self.submit(contest, self.alice, self.problems[0], "AC", 20, score=100)

        rows = scoreboard.build(contest)["rows"]
        self.assertEqual(rows[0].user, self.alice)
        self.assertEqual(rows[1].user, self.bob)
        # equal totals but different times still get distinct ranks only if
        # the tie-break separates them; ranks are shared on equal totals
        self.assertEqual(rows[0].rank, 1)
        self.assertEqual(rows[1].rank, 1)

    # --- freeze ---------------------------------------------------------

    def test_freeze_hides_late_results_from_students_but_not_staff(self):
        # end in 10 minutes, freeze the last 30 -> we are inside the freeze
        contest = self.make_contest(Contest.Scoring.ICPC, freeze_minutes=30)
        contest.end_time = timezone.now() + timedelta(minutes=10)
        contest.save()
        self.assertTrue(contest.is_frozen)

        minutes_now = int((timezone.now() - self.start).total_seconds() // 60)
        self.submit(contest, self.alice, self.problems[0], "AC", 10)
        self.submit(contest, self.alice, self.problems[1], "AC", minutes_now - 1)

        student = scoreboard.build(contest, viewer=self.bob)
        row = next(r for r in student["rows"] if r.user == self.alice)
        self.assertTrue(student["frozen"])
        self.assertEqual(row.solved, 1)              # the late solve is hidden
        self.assertEqual(row.cells["B"].pending, 1)

        staff = User.objects.create_user("teacher", is_staff=True)
        live = scoreboard.build(contest, viewer=staff)
        row = next(r for r in live["rows"] if r.user == self.alice)
        self.assertFalse(live["frozen"])
        self.assertEqual(row.solved, 2)

    def test_practice_submissions_never_reach_the_scoreboard(self):
        contest = self.make_contest(Contest.Scoring.ICPC)
        submission = self.submit(contest, self.alice, self.problems[0], "AC", 10)
        Submission.objects.filter(pk=submission.pk).update(counts_for_ranking=False)

        row = next(r for r in scoreboard.build(contest)["rows"]
                   if r.user == self.alice)
        self.assertEqual(row.solved, 0)

    def test_hidden_participants_are_off_the_public_board(self):
        contest = self.make_contest(Contest.Scoring.IOI)
        Participation.objects.filter(contest=contest, user=self.bob).update(
            is_hidden=True)
        usernames = [r.user.username for r in scoreboard.build(contest)["rows"]]
        self.assertEqual(usernames, ["alice"])


class StatementRenderingTests(TestCase):
    """Maths must survive Markdown, and untrusted HTML must not survive at all."""

    def render(self, text, fmt="md"):
        from core.rendering import render_statement
        return render_statement(text, fmt)

    def test_math_is_not_mangled_by_markdown(self):
        out = self.render(r"$a_i \le 10^5$ and $b_j$")
        self.assertIn(r"$a_i \le 10^5$", out)
        self.assertIn("$b_j$", out)
        self.assertNotIn("<em>", out)

    def test_emphasis_still_works_outside_math(self):
        out = self.render("$a_i$ and _emphasis_")
        self.assertIn("<em>emphasis</em>", out)
        self.assertIn("$a_i$", out)

    def test_display_math_survives(self):
        out = self.render(r"$$\sum_{i=1}^{n} a_i$$")
        self.assertIn(r"\sum_{i=1}^{n}", out)

    def test_scripts_are_stripped(self):
        out = self.render("hi <script>alert(1)</script> there", "html")
        self.assertNotIn("script", out.lower())
        self.assertIn("hi", out)

    def test_javascript_urls_are_stripped(self):
        out = self.render('<a href="javascript:alert(1)">x</a>', "html")
        self.assertNotIn("javascript:", out)

    def test_event_handlers_are_stripped(self):
        out = self.render('<p onclick="evil()">text</p>', "html")
        self.assertNotIn("onclick", out)
        self.assertIn("text", out)

    def test_greek_is_preserved(self):
        out = self.render("Υπολογίστε το **άθροισμα**")
        self.assertIn("Υπολογίστε", out)
        self.assertIn("<strong>άθροισμα</strong>", out)


class StatementAccessTests(TestCase):
    """A contest statement PDF must not be reachable before the contest opens."""

    def setUp(self):
        from core.models import Statement
        self.problem = Problem.objects.create(code="secret", title="Secret",
                                              is_public=False, max_score=100)
        Statement.objects.create(problem=self.problem, language="el",
                                 body="κείμενο", is_default=True)
        self.student = User.objects.create_user("student", password="pw-12345678")

    def test_unstarted_contest_problem_is_sealed(self):
        contest = Contest.objects.create(
            name="Later", slug="later",
            start_time=timezone.now() + timedelta(hours=2),
            end_time=timezone.now() + timedelta(hours=5), is_listed=True)
        ContestProblem.objects.create(contest=contest, problem=self.problem, label="A")
        Participation.objects.create(contest=contest, user=self.student)

        self.client.login(username="student", password="pw-12345678")
        page = self.client.get("/contests/later/A/")
        self.assertContains(page, "sealed")
        self.assertNotContains(page, "κείμενο")
        # and the PDF route must refuse too, not just the page
        self.assertEqual(self.client.get("/contests/later/A/statement/el.pdf").status_code,
                         404)

    def test_non_public_problem_is_not_readable_directly(self):
        self.client.login(username="student", password="pw-12345678")
        self.assertEqual(self.client.get("/problems/secret/").status_code, 404)
        self.assertEqual(
            self.client.get("/problems/secret/statement/el.pdf").status_code, 404)


class SubmissionCapTests(TestCase):
    """The hourly cap must stop a flood without blocking normal use."""

    def setUp(self):
        from django.test import override_settings
        self.problem = Problem.objects.create(code="cap", title="Cap",
                                              is_public=True, max_score=100)
        (self.problem.package_dir).mkdir(parents=True, exist_ok=True)
        import json as _json
        (self.problem.package_dir / "problem.json").write_text(_json.dumps({
            "code": "cap", "subtasks": [
                {"index": 1, "points": 100, "aggregation": "min", "tests": ["01"]}]}))
        tests = self.problem.package_dir / "tests"
        tests.mkdir(exist_ok=True)
        (tests / "01.in").write_text("1\n")
        (tests / "01.ans").write_text("1\n")
        self.user = User.objects.create_user("flood", password="pw-12345678")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.problem.package_dir, ignore_errors=True)

    def test_hourly_cap_blocks_after_limit(self):
        from django.test import override_settings
        # No cooldown, cap of 3, so we can hit the cap deterministically.
        with override_settings(SUBMIT_COOLDOWN_SECONDS=0, SUBMIT_HOURLY_CAP=3,
                               OJ_JUDGE_INLINE=False, JUDGE_INLINE=False):
            self.client.login(username="flood", password="pw-12345678")
            made = 0
            for _ in range(3):
                self.client.post("/problems/cap/submit/",
                                 {"language": "cpp17", "source": "int main(){}"})
                made += 1
            self.assertEqual(Submission.objects.filter(user=self.user).count(), 3)
            # The 4th within the hour is refused (redirect, no new row).
            self.client.post("/problems/cap/submit/",
                             {"language": "cpp17", "source": "int main(){}"})
            self.assertEqual(Submission.objects.filter(user=self.user).count(), 3)

    def test_staff_are_exempt_from_the_cap(self):
        from django.test import override_settings
        staff = User.objects.create_user("teach", password="pw-12345678",
                                         is_staff=True)
        with override_settings(SUBMIT_COOLDOWN_SECONDS=0, SUBMIT_HOURLY_CAP=2,
                               JUDGE_INLINE=False):
            self.client.login(username="teach", password="pw-12345678")
            for _ in range(4):
                self.client.post("/problems/cap/submit/",
                                 {"language": "cpp17", "source": "int main(){}"})
            self.assertEqual(Submission.objects.filter(user=staff).count(), 4)


class RunCodeTests(TestCase):
    """Run Code: compile + run on custom input, ungraded, rate-limited."""

    def setUp(self):
        import json as _json
        self.problem = Problem.objects.create(code="runp", title="Run",
                                              is_public=True, max_score=100)
        self.problem.package_dir.mkdir(parents=True, exist_ok=True)
        (self.problem.package_dir / "problem.json").write_text(_json.dumps({
            "code": "runp", "subtasks": [
                {"index": 1, "points": 100, "aggregation": "min", "tests": ["01"]}]}))
        t = self.problem.package_dir / "tests"; t.mkdir(exist_ok=True)
        (t / "01.in").write_text("1\n"); (t / "01.ans").write_text("1\n")
        self.user = User.objects.create_user("runner", password="pw-12345678")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.problem.package_dir, ignore_errors=True)

    def _post(self, client, **kw):
        data = {"language": "cpp17", "source": "int main(){}", "stdin": ""}
        data.update(kw)
        return client.post("/problems/runp/run/", data)

    def test_successful_run_returns_stdout(self):
        from django.test import override_settings
        with override_settings(OJ_SANDBOX="rlimit"):
            self.client.login(username="runner", password="pw-12345678")
            src = ('#include <iostream>\nint main(){int a,b;std::cin>>a>>b;'
                   'std::cout<<a+b;}')
            r = self._post(self.client, source=src, stdin="2 3\n")
            d = r.json()
            self.assertEqual(d["status"], "ok")
            self.assertEqual(d["stdout"].strip(), "5")

    def test_compile_error_reported(self):
        self.client.login(username="runner", password="pw-12345678")
        r = self._post(self.client, source="int main(){ broken")
        self.assertEqual(r.json()["status"], "compile_error")

    def test_empty_source_rejected(self):
        self.client.login(username="runner", password="pw-12345678")
        self.assertEqual(self._post(self.client, source="  ").json()["status"], "error")

    def test_get_not_allowed(self):
        self.client.login(username="runner", password="pw-12345678")
        self.assertEqual(self.client.get("/problems/runp/run/").status_code, 405)

    def test_login_required(self):
        r = self._post(self.client)
        self.assertIn(r.status_code, (302, 301))

    def test_per_minute_rate_limit(self):
        from django.test import override_settings
        with override_settings(RUN_PER_MINUTE=2, RUN_HOURLY_CAP=100, OJ_SANDBOX="rlimit"):
            self.client.login(username="runner", password="pw-12345678")
            got = [self._post(self.client, source="int main(){}").json()["status"]
                   for _ in range(4)]
            self.assertEqual(got.count("rate_limited"), 2)

    def test_staff_exempt_from_rate_limit(self):
        from django.test import override_settings
        staff = User.objects.create_user("runstaff", password="pw-12345678",
                                         is_staff=True)
        with override_settings(RUN_PER_MINUTE=1, RUN_HOURLY_CAP=1, OJ_SANDBOX="rlimit"):
            self.client.login(username="runstaff", password="pw-12345678")
            got = [self._post(self.client, source="int main(){}").json()["status"]
                   for _ in range(3)]
            self.assertNotIn("rate_limited", got)

    def test_run_does_not_count_as_submission(self):
        from django.test import override_settings
        with override_settings(OJ_SANDBOX="rlimit"):
            self.client.login(username="runner", password="pw-12345678")
            self._post(self.client, source="int main(){}")
            self.assertEqual(Submission.objects.filter(user=self.user).count(), 0)


class RunCodeSampleTests(TestCase):
    """Empty custom-input box runs against the problem's sample input."""

    ECHO_FIRST = ('#include <iostream>\nint main(){long long n;std::cin>>n;'
                  'std::cout<<n;}')  # prints the first number it reads

    def _make(self, code, spec_extra=None):
        import json as _json
        p = Problem.objects.create(code=code, title=code, is_public=True, max_score=100)
        p.package_dir.mkdir(parents=True, exist_ok=True)
        spec = {"code": code, "subtasks": [
            {"index": 1, "points": 100, "aggregation": "min", "tests": ["01"]}]}
        if spec_extra:
            spec.update(spec_extra)
        (p.package_dir / "problem.json").write_text(_json.dumps(spec))
        t = p.package_dir / "tests"; t.mkdir(exist_ok=True)
        (t / "01.in").write_text("42\n7\n")
        (t / "01.ans").write_text("42\n")
        return p

    def tearDown(self):
        import shutil
        for p in Problem.objects.all():
            shutil.rmtree(p.package_dir, ignore_errors=True)

    def test_empty_box_uses_first_test(self):
        from django.test import override_settings
        p = self._make("samp1")
        u = User.objects.create_user("s1", password="pw-12345678")
        with override_settings(OJ_SANDBOX="rlimit"):
            self.client.login(username="s1", password="pw-12345678")
            r = self.client.post("/problems/samp1/run/",
                                 {"language": "cpp17", "source": self.ECHO_FIRST,
                                  "stdin": ""})
            d = r.json()
            self.assertEqual(d["status"], "ok")
            self.assertEqual(d["stdout"].strip(), "42")   # first number of 01.in

    def test_explicit_input_overrides_sample(self):
        from django.test import override_settings
        p = self._make("samp2")
        u = User.objects.create_user("s2", password="pw-12345678")
        with override_settings(OJ_SANDBOX="rlimit"):
            self.client.login(username="s2", password="pw-12345678")
            r = self.client.post("/problems/samp2/run/",
                                 {"language": "cpp17", "source": self.ECHO_FIRST,
                                  "stdin": "99\n"})
            self.assertEqual(r.json()["stdout"].strip(), "99")

    def test_explicit_sample_input_field_wins(self):
        from django.test import override_settings
        p = self._make("samp3", {"sample_input": "500\n"})
        u = User.objects.create_user("s3", password="pw-12345678")
        with override_settings(OJ_SANDBOX="rlimit"):
            self.client.login(username="s3", password="pw-12345678")
            r = self.client.post("/problems/samp3/run/",
                                 {"language": "cpp17", "source": self.ECHO_FIRST,
                                  "stdin": ""})
            self.assertEqual(r.json()["stdout"].strip(), "500")

    def test_hide_sample_input_opt_out(self):
        from django.test import override_settings
        p = self._make("samp4", {"hide_sample_input": True})
        u = User.objects.create_user("s4", password="pw-12345678")
        with override_settings(OJ_SANDBOX="rlimit"):
            self.client.login(username="s4", password="pw-12345678")
            # empty box, no sample offered -> program reads nothing -> prints 0/garbage,
            # but crucially the hidden 42 is NOT fed in.
            r = self.client.post("/problems/samp4/run/",
                                 {"language": "cpp17", "source": self.ECHO_FIRST,
                                  "stdin": ""})
            self.assertNotEqual(r.json().get("stdout", "").strip(), "42")

    def test_sample_not_in_page_when_hidden(self):
        p = self._make("samp5", {"hide_sample_input": True})
        u = User.objects.create_user("s5", password="pw-12345678")
        self.client.login(username="s5", password="pw-12345678")
        html = self.client.get("/problems/samp5/").content.decode()
        self.assertNotIn("42", html.split("run-stdin")[1][:200]
                         if "run-stdin" in html else "")
