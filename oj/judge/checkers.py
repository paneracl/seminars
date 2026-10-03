"""
Output checkers.

  token   -- default. Whitespace-insensitive token comparison. Trailing
             newlines, trailing spaces and CRLF do not cost a student points.
  exact   -- byte-for-byte after normalising line endings. Use sparingly.
  float   -- token comparison where numeric tokens compare within a tolerance
             (absolute OR relative, the usual convention).
  custom  -- an external program (testlib-style):
                 checker <input> <contestant_output> <answer>
             exit 0 = accepted, 1 = wrong answer, 2 = presentation error,
             3 = checker failure. A partial score may be written to stderr as
             a bare float in [0,1] (testlib's quitp convention).

Every checker returns a CheckResult with a score in [0,1], so partial-credit
tasks and all-or-nothing tasks share one code path.
"""

from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CheckResult:
    score: float          # 0.0 .. 1.0
    message: str = ""

    @property
    def accepted(self) -> bool:
        return self.score >= 1.0 - 1e-9


def _tokens(path: Path) -> list[str]:
    return path.read_text(errors="replace").split()


def _normalise(path: Path) -> str:
    text = path.read_text(errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n")


def check_token(_input: Path, output: Path, answer: Path, **_) -> CheckResult:
    got, want = _tokens(output), _tokens(answer)
    if got == want:
        return CheckResult(1.0)
    if len(got) != len(want):
        return CheckResult(0.0, f"expected {len(want)} tokens, got {len(got)}")
    for index, (a, b) in enumerate(zip(got, want)):
        if a != b:
            return CheckResult(0.0, f"token {index + 1}: expected {b!r}, got {a!r}")
    return CheckResult(0.0, "outputs differ")


def check_exact(_input: Path, output: Path, answer: Path, **_) -> CheckResult:
    if _normalise(output) == _normalise(answer):
        return CheckResult(1.0)
    return CheckResult(0.0, "output does not match exactly")


def check_float(_input: Path, output: Path, answer: Path,
                epsilon: float = 1e-6, **_) -> CheckResult:
    got, want = _tokens(output), _tokens(answer)
    if len(got) != len(want):
        return CheckResult(0.0, f"expected {len(want)} tokens, got {len(got)}")
    for index, (a, b) in enumerate(zip(got, want)):
        if a == b:
            continue
        try:
            fa, fb = float(a), float(b)
        except ValueError:
            return CheckResult(0.0, f"token {index + 1}: expected {b!r}, got {a!r}")
        if math.isnan(fa) or math.isinf(fa):
            return CheckResult(0.0, f"token {index + 1}: not a finite number")
        delta = abs(fa - fb)
        if delta > epsilon and delta > epsilon * abs(fb):
            return CheckResult(
                0.0, f"token {index + 1}: expected {fb!r}, got {fa!r} (delta {delta:.3g})"
            )
    return CheckResult(1.0)


def check_custom(input_path: Path, output: Path, answer: Path,
                 checker_binary: Path | None = None,
                 timeout: float = 10.0, **_) -> CheckResult:
    if checker_binary is None or not Path(checker_binary).exists():
        return CheckResult(0.0, "checker binary missing")
    proc = subprocess.run(
        [str(checker_binary), str(input_path), str(output), str(answer)],
        capture_output=True, text=True, timeout=timeout,
    )
    stderr = (proc.stderr or "").strip()
    if proc.returncode == 0:
        return CheckResult(1.0, stderr[:500])
    if proc.returncode in (1, 2):
        # testlib quitp writes a bare fraction to stderr for partial credit
        first = stderr.split()[0] if stderr else ""
        try:
            partial = float(first)
            if 0.0 <= partial <= 1.0:
                return CheckResult(partial, stderr[:500])
        except ValueError:
            pass
        return CheckResult(0.0, stderr[:500] or "wrong answer")
    raise RuntimeError(f"checker failed (exit {proc.returncode}): {stderr[:500]}")


CHECKERS = {
    "token": check_token,
    "exact": check_exact,
    "float": check_float,
    "custom": check_custom,
}


def run_checker(spec: dict, input_path: Path, output: Path, answer: Path,
                checker_binary: Path | None = None) -> CheckResult:
    kind = spec.get("type", "token")
    if kind not in CHECKERS:
        raise ValueError(f"unknown checker type {kind!r}")
    options = {k: v for k, v in spec.items() if k != "type"}
    return CHECKERS[kind](input_path, output, answer,
                          checker_binary=checker_binary, **options)
