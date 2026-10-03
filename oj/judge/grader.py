"""
The grader: compile a submission, run it against a problem's tests, aggregate
into subtask scores.

Scoring is unified. Every problem has subtasks. An ICPC-style problem is just
one subtask worth 100 points with `min` aggregation over all tests. The
contest layer decides how to *rank* those scores (max-sum vs solved+penalty)
and how much feedback to reveal; the grader always computes the same thing.

Feedback policy is applied here rather than in the web layer, because the
safest place to withhold hidden-test detail is before it leaves the worker.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from . import checkers, languages
from .sandbox import RunLimits, RunStatus, Sandbox, open_sandbox


class Verdict(str, Enum):
    ACCEPTED = "AC"
    WRONG_ANSWER = "WA"
    PARTIAL = "PA"
    TIME_LIMIT = "TLE"
    MEMORY_LIMIT = "MLE"
    RUNTIME_ERROR = "RE"
    COMPILE_ERROR = "CE"
    OUTPUT_LIMIT = "OLE"
    JUDGE_ERROR = "IE"
    SKIPPED = "SK"


class Feedback(str, Enum):
    FULL = "full"           # practice: show every test
    SUBTASK = "subtask"     # IOI contest: per-subtask scores, no test detail
    FIRST_FAIL = "first"    # ICPC contest: verdict + index of first failure
    MINIMAL = "minimal"     # verdict only


@dataclass
class TestOutcome:
    name: str
    verdict: Verdict
    score: float = 0.0
    cpu_time: float = 0.0
    memory_kb: int = 0
    message: str = ""


@dataclass
class SubtaskOutcome:
    index: int
    points: float
    awarded: float = 0.0
    verdict: Verdict = Verdict.SKIPPED
    tests: list[TestOutcome] = field(default_factory=list)


@dataclass
class JudgeReport:
    verdict: Verdict
    score: float = 0.0
    max_score: float = 0.0
    cpu_time: float = 0.0
    memory_kb: int = 0
    compile_output: str = ""
    subtasks: list[SubtaskOutcome] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_STATUS_TO_VERDICT = {
    RunStatus.TIMED_OUT: Verdict.TIME_LIMIT,
    RunStatus.MEMORY_EXCEEDED: Verdict.MEMORY_LIMIT,
    RunStatus.OUTPUT_EXCEEDED: Verdict.OUTPUT_LIMIT,
    RunStatus.KILLED_BY_SIGNAL: Verdict.RUNTIME_ERROR,
    RunStatus.NONZERO_EXIT: Verdict.RUNTIME_ERROR,
    RunStatus.SANDBOX_ERROR: Verdict.JUDGE_ERROR,
}

# Worst-first ordering used to pick the headline verdict for a submission.
_SEVERITY = [
    Verdict.JUDGE_ERROR, Verdict.COMPILE_ERROR, Verdict.MEMORY_LIMIT,
    Verdict.TIME_LIMIT, Verdict.RUNTIME_ERROR, Verdict.OUTPUT_LIMIT,
    Verdict.WRONG_ANSWER, Verdict.PARTIAL, Verdict.ACCEPTED, Verdict.SKIPPED,
]


class Problem:
    """A problem package on disk."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.spec = json.loads((self.root / "problem.json").read_text())
        self.tests_dir = self.root / self.spec.get("tests_dir", "tests")

    @property
    def code(self) -> str:
        return self.spec["code"]

    @property
    def time_limit(self) -> float:
        return float(self.spec.get("time_limit", 1.0))

    @property
    def memory_limit_mb(self) -> int:
        return int(self.spec.get("memory_limit_mb", 256))

    @property
    def checker_spec(self) -> dict:
        return self.spec.get("checker", {"type": "token"})

    @property
    def subtasks(self) -> list[dict]:
        declared = self.spec.get("subtasks")
        if declared:
            return declared
        # No subtasks declared -> single all-or-nothing subtask (ICPC style).
        names = sorted(p.stem for p in self.tests_dir.glob("*.in"))
        return [{"index": 1, "points": 100, "aggregation": "min", "tests": names}]

    def test_paths(self, name: str) -> tuple[Path, Path]:
        return self.tests_dir / f"{name}.in", self.tests_dir / f"{name}.ans"

    def sample_input(self) -> str | None:
        """Input to prefill Run Code when the student leaves the box empty.

        Order of preference:
          1. An explicit "sample_input" string in problem.json (never leaks a
             hidden file -- the teacher wrote exactly what to show).
          2. The first test's input file, unless the problem sets
             "hide_sample_input": true. This is convenient because the first
             test is almost always the worked example from the statement, but
             it does reveal that one input, so a problem whose very first test
             is secret can opt out.

        Returns None if no sample can be safely offered.
        """
        explicit = self.spec.get("sample_input")
        if isinstance(explicit, str) and explicit.strip():
            return explicit
        if self.spec.get("hide_sample_input"):
            return None
        subtasks = self.subtasks
        if not subtasks or not subtasks[0].get("tests"):
            return None
        first = subtasks[0]["tests"][0]
        in_path, _ = self.test_paths(first)
        try:
            return in_path.read_text()
        except OSError:
            return None

    def limits_for(self, lang: languages.Language, *, compiling: bool = False) -> RunLimits:
        override = self.spec.get("language_overrides", {}).get(lang.key, {})
        multiplier = float(override.get("time_multiplier", lang.time_multiplier))
        extra_mb = int(override.get("memory_extra_mb", lang.memory_extra_mb))
        if compiling:
            return RunLimits(cpu_time=20.0, wall_time=40.0, memory_mb=1024,
                             max_processes=lang.compile_processes, max_output_mb=64)
        return RunLimits(
            cpu_time=round(self.time_limit * multiplier, 3),
            memory_mb=self.memory_limit_mb + extra_mb,
            max_processes=lang.run_processes,
            max_output_mb=int(self.spec.get("output_limit_mb", 64)),
        )


def judge(problem: Problem, source: str, language_key: str, *,
          box_id: int = 0, feedback: Feedback = Feedback.FULL,
          sandbox_factory=open_sandbox) -> JudgeReport:
    lang = languages.get(language_key)
    report = JudgeReport(verdict=Verdict.JUDGE_ERROR)
    report.max_score = sum(float(s["points"]) for s in problem.subtasks)

    sandbox: Sandbox = sandbox_factory(box_id)
    try:
        sandbox.write(lang.source_name, source)

        compile_argv = lang.compile_argv()
        if compile_argv:
            compiled = sandbox.run(compile_argv, problem.limits_for(lang, compiling=True))
            if not compiled.ok:
                report.verdict = Verdict.COMPILE_ERROR
                detail = compiled.stderr_text
                if compiled.status is RunStatus.TIMED_OUT:
                    detail = "Compilation timed out."
                report.compile_output = _trim(detail)
                return report
            report.compile_output = _trim(compiled.stderr_text)  # warnings, if any

        checker_binary = _prepare_checker(problem)
        limits = problem.limits_for(lang)
        stop_early = feedback is Feedback.FIRST_FAIL

        aborted = False
        for spec in problem.subtasks:
            outcome = _run_subtask(problem, sandbox, lang, spec, limits,
                                   checker_binary, skip=aborted)
            report.subtasks.append(outcome)
            report.score += outcome.awarded
            for test in outcome.tests:
                report.cpu_time = max(report.cpu_time, test.cpu_time)
                report.memory_kb = max(report.memory_kb, test.memory_kb)
            if stop_early and outcome.verdict is not Verdict.ACCEPTED:
                aborted = True

        report.verdict = _headline(report)
        _apply_feedback(report, feedback)
        return report

    except Exception as exc:                      # noqa: BLE001 - reported, not swallowed
        report.verdict = Verdict.JUDGE_ERROR
        report.message = f"{type(exc).__name__}: {exc}"
        return report
    finally:
        sandbox.close()


def _run_subtask(problem, sandbox, lang, spec, limits, checker_binary, *, skip):
    outcome = SubtaskOutcome(index=int(spec["index"]), points=float(spec["points"]))
    if skip:
        outcome.verdict = Verdict.SKIPPED
        return outcome

    aggregation = spec.get("aggregation", "min")
    fractions: list[float] = []

    for name in spec["tests"]:
        input_path, answer_path = problem.test_paths(name)
        sandbox.put(input_path, "input.txt")

        run = sandbox.run(lang.run_argv(), limits, stdin="input.txt", stdout="output.txt")
        test = TestOutcome(name=name, cpu_time=run.cpu_time, memory_kb=run.memory_kb,
                           verdict=Verdict.ACCEPTED)

        if not run.ok:
            test.verdict = _STATUS_TO_VERDICT.get(run.status, Verdict.RUNTIME_ERROR)
            test.message = _runtime_hint(run)
        else:
            try:
                result = checkers.run_checker(problem.checker_spec, input_path,
                                              run.stdout_path, answer_path,
                                              checker_binary)
                test.score = result.score
                test.message = result.message
                if result.accepted:
                    test.verdict = Verdict.ACCEPTED
                elif result.score > 0:
                    test.verdict = Verdict.PARTIAL
                else:
                    test.verdict = Verdict.WRONG_ANSWER
            except Exception as exc:              # noqa: BLE001
                test.verdict = Verdict.JUDGE_ERROR
                test.message = str(exc)

        if test.verdict is Verdict.ACCEPTED:
            test.score = 1.0
        elif test.verdict is not Verdict.PARTIAL:
            test.score = 0.0

        fractions.append(test.score)
        outcome.tests.append(test)

        # min-aggregated subtasks cannot recover once a test scores zero
        if aggregation == "min" and test.score == 0.0:
            break

    if not fractions:
        outcome.verdict = Verdict.SKIPPED
        return outcome

    if aggregation == "min":
        fraction = min(fractions)
    elif aggregation == "sum":
        fraction = sum(fractions) / len(spec["tests"])
    else:
        raise ValueError(f"unknown aggregation {aggregation!r}")

    outcome.awarded = round(outcome.points * fraction, 6)
    outcome.verdict = _headline_of(t.verdict for t in outcome.tests)
    return outcome


def _headline_of(verdicts) -> Verdict:
    seen = set(verdicts)
    for candidate in _SEVERITY:
        if candidate in seen:
            return candidate
    return Verdict.SKIPPED


def _headline(report: JudgeReport) -> Verdict:
    all_tests = [t.verdict for s in report.subtasks for t in s.tests]
    worst = _headline_of(all_tests)
    if worst is Verdict.ACCEPTED and report.score < report.max_score - 1e-9:
        return Verdict.PARTIAL
    if worst is not Verdict.ACCEPTED and report.score > 1e-9:
        return Verdict.PARTIAL
    return worst


def _apply_feedback(report: JudgeReport, feedback: Feedback) -> None:
    if feedback is Feedback.FULL:
        return
    if feedback is Feedback.SUBTASK:
        for subtask in report.subtasks:
            subtask.tests = []
        return
    if feedback is Feedback.FIRST_FAIL:
        for subtask in report.subtasks:
            kept = []
            for test in subtask.tests:
                stripped = TestOutcome(name=test.name, verdict=test.verdict)
                kept.append(stripped)
                if test.verdict is not Verdict.ACCEPTED:
                    break
            subtask.tests = kept
        return
    report.subtasks = []


def _runtime_hint(run) -> str:
    if run.status is RunStatus.TIMED_OUT:
        return ""
    if run.status is RunStatus.MEMORY_EXCEEDED:
        return ""
    if run.status is RunStatus.OUTPUT_EXCEEDED:
        return "Output limit exceeded"
    if run.status is RunStatus.SANDBOX_ERROR:
        return "Judge error; this is not your fault"
    if run.signal == 11:
        return "Segmentation fault (signal 11)"
    if run.signal == 6:
        return "Aborted (signal 6)"
    if run.signal:
        return f"Killed by signal {run.signal}"
    if run.exit_code:
        return f"Exited with code {run.exit_code}"
    return ""


def _prepare_checker(problem: Problem) -> Path | None:
    """Compile a custom checker once and cache the binary next to its source."""
    if problem.checker_spec.get("type") != "custom":
        return None
    source = problem.root / problem.checker_spec.get("source", "checker/checker.cpp")
    binary = source.with_suffix("")
    if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    import subprocess
    proc = subprocess.run(
        ["/usr/bin/g++", "-std=gnu++17", "-O2", "-w", "-I", str(source.parent),
         "-o", str(binary), str(source)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"checker did not compile: {proc.stderr[:500]}")
    return binary


def _trim(text: str, limit: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


@dataclass
class RunResult:
    """Result of a one-off Run Code: compile once, run against custom input.

    This is deliberately not judging -- there is no expected answer and no
    score. It exists so a student can confirm their program compiles and see
    what it prints on their own input before spending a submission.
    """
    status: str                     # "ok" | "compile_error" | "runtime_error"
                                    # | "timeout" | "memory" | "output_limit" | "error"
    compile_output: str = ""
    stdout: str = ""
    stderr: str = ""
    cpu_time: float = 0.0
    memory_kb: int = 0
    truncated: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "compile_output": self.compile_output,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "cpu_time": round(self.cpu_time, 3),
            "memory_kb": self.memory_kb,
            "truncated": self.truncated,
            "message": self.message,
        }


# How much captured output to send back to the browser. The sandbox already
# caps how much a program may *write* (fsize); this caps how much we *return*,
# so a 60 MB legitimate output doesn't get shipped into a web response.
_RUN_OUTPUT_CHARS = 32 * 1024

_RUN_STATUS = {
    RunStatus.TIMED_OUT: "timeout",
    RunStatus.MEMORY_EXCEEDED: "memory",
    RunStatus.OUTPUT_EXCEEDED: "output_limit",
    RunStatus.KILLED_BY_SIGNAL: "runtime_error",
    RunStatus.NONZERO_EXIT: "runtime_error",
    RunStatus.SANDBOX_ERROR: "error",
}


def run_once(problem: Problem, source: str, language_key: str, stdin_text: str, *,
             box_id: int = 0, sandbox_factory=open_sandbox) -> RunResult:
    """Compile and run a submission against caller-supplied input, once.

    Uses the same sandbox, language config, and limit scaling as judging, so
    behaviour here matches behaviour under grading. Never raises: any internal
    failure comes back as status 'error'.
    """
    try:
        lang = languages.get(language_key)
    except KeyError as exc:
        return RunResult(status="error", message=str(exc))

    sandbox: Sandbox = sandbox_factory(box_id)
    try:
        sandbox.write(lang.source_name, source)

        compile_argv = lang.compile_argv()
        if compile_argv:
            compiled = sandbox.run(compile_argv, problem.limits_for(lang, compiling=True))
            if not compiled.ok:
                detail = compiled.stderr_text
                if compiled.status is RunStatus.TIMED_OUT:
                    detail = "Compilation timed out."
                return RunResult(status="compile_error",
                                 compile_output=_trim(detail))
            compile_warnings = _trim(compiled.stderr_text)
        else:
            compile_warnings = ""

        sandbox.write("input.txt", stdin_text if stdin_text is not None else "")
        limits = problem.limits_for(lang)
        run = sandbox.run(lang.run_argv(), limits, stdin="input.txt", stdout="output.txt")

        out_text, out_trunc = _read_capped(run.stdout_path, _RUN_OUTPUT_CHARS)
        err_text = (run.stderr_text or "")[:_RUN_OUTPUT_CHARS]

        result = RunResult(
            compile_output=compile_warnings,
            stdout=out_text,
            stderr=err_text,
            cpu_time=run.cpu_time,
            memory_kb=run.memory_kb,
            truncated=out_trunc,
            status="ok" if run.ok else _RUN_STATUS.get(run.status, "runtime_error"),
        )
        if result.status == "runtime_error":
            result.message = _runtime_hint(run)
        elif result.status == "timeout":
            result.message = f"Exceeded the time limit ({limits.cpu_time:g}s)."
        elif result.status == "memory":
            result.message = f"Exceeded the memory limit ({limits.memory_mb} MB)."
        elif result.status == "output_limit":
            result.message = f"Wrote more than the output limit ({limits.max_output_mb} MB)."
        return result

    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        return RunResult(status="error", message=f"{type(exc).__name__}: {exc}")
    finally:
        sandbox.close()


def _read_capped(path, limit: int) -> tuple[str, bool]:
    if path is None:
        return "", False
    try:
        with open(path, "r", errors="replace") as fh:
            data = fh.read(limit + 1)
    except OSError:
        return "", False
    if len(data) > limit:
        return data[:limit], True
    return data, False
