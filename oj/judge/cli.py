"""
Command-line judging, for validating a problem package before it goes near a
contest.

    python3 -m judge.cli problems/sumsub solutions/model.cpp
    python3 -m judge.cli problems/sumsub solutions/brute.py --expect PA
    python3 -m judge.cli problems/sumsub sol.cpp --feedback subtask --json

The --expect flag is the one worth building a habit around: keep a model
solution, a brute force, and two or three deliberately-wrong solutions beside
each problem, each with the verdict it should receive. Then a single pass
tells you whether your subtask boundaries actually separate the solutions you
intended them to separate. Most broken olympiad tasks are broken this way.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .grader import Feedback, Problem, Verdict, judge
from .languages import guess_from_filename


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="judge.cli")
    parser.add_argument("problem", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--language", "-l", default=None)
    parser.add_argument("--feedback", "-f", default="full",
                        choices=[f.value for f in Feedback])
    parser.add_argument("--expect", "-e", default=None,
                        help="verdict this solution should get, e.g. AC / PA / TLE")
    parser.add_argument("--box-id", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    language = args.language or guess_from_filename(args.source.name)
    if language is None:
        print(f"cannot infer language from {args.source.name}", file=sys.stderr)
        return 2

    problem = Problem(args.problem)
    report = judge(problem, args.source.read_text(), language,
                   box_id=args.box_id, feedback=Feedback(args.feedback))

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        _print_human(report)

    if args.expect:
        expected = args.expect.upper()
        if report.verdict.value != expected:
            print(f"\nFAIL: expected {expected}, got {report.verdict.value}",
                  file=sys.stderr)
            return 1
        print(f"\nOK: got expected verdict {expected}")
    return 0


def _print_human(report) -> None:
    print(f"{report.verdict.value}   {report.score:g}/{report.max_score:g} points"
          f"   max {report.cpu_time:.3f}s")
    if report.compile_output:
        print("\ncompiler:")
        for line in report.compile_output.splitlines()[:20]:
            print("  " + line)
    if report.message:
        print(f"\nnote: {report.message}")
    for subtask in report.subtasks:
        print(f"\n  subtask {subtask.index}: {subtask.awarded:g}/{subtask.points:g}"
              f"  [{subtask.verdict.value}]")
        for test in subtask.tests:
            extra = f"  {test.message}" if test.message else ""
            print(f"    {test.name:>6}  {test.verdict.value:<4} "
                  f"{test.cpu_time:6.3f}s{extra}")


if __name__ == "__main__":
    raise SystemExit(main())
