"""
Importing problem packages.

A package is a zip (or a directory) containing:

    problem.json
    tests/01.in  tests/01.ans  ...
    checker/checker.cpp        (optional)
    statement.html             (optional; Greek welcome)

Import validates before it writes anything. The failure mode worth guarding
against is a package that imports cleanly and *then* turns out to reference a
test file that doesn't exist -- because you find that out mid-contest, on a
student's submission, as a Judge Error.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.conf import settings

from judge.checkers import CHECKERS


class PackageError(Exception):
    """Raised with a human-readable explanation of what is wrong."""


def _validate(root: Path) -> dict:
    spec_path = root / "problem.json"
    if not spec_path.exists():
        raise PackageError("problem.json not found at the top level of the package. "
                           "Zip the *contents* of the problem folder, not the folder.")
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"problem.json is not valid JSON: {exc}") from exc

    if not spec.get("code"):
        raise PackageError('problem.json must contain a "code" field.')

    tests_dir = root / spec.get("tests_dir", "tests")
    if not tests_dir.is_dir():
        raise PackageError(f"tests directory {tests_dir.name!r} not found.")

    checker = spec.get("checker", {"type": "token"})
    if checker.get("type") not in CHECKERS:
        raise PackageError(f"unknown checker type {checker.get('type')!r}; "
                           f"expected one of {sorted(CHECKERS)}")
    if checker.get("type") == "custom":
        source = root / checker.get("source", "checker/checker.cpp")
        if not source.exists():
            raise PackageError(f"custom checker source not found: {source.name}")

    available = {p.stem for p in tests_dir.glob("*.in")}
    if not available:
        raise PackageError("no .in files found in the tests directory.")

    subtasks = spec.get("subtasks")
    if not subtasks:
        subtasks = [{"index": 1, "points": 100, "aggregation": "min",
                     "tests": sorted(available)}]

    seen: set[str] = set()
    for subtask in subtasks:
        for field in ("index", "points", "tests"):
            if field not in subtask:
                raise PackageError(f"subtask is missing {field!r}: {subtask}")
        if subtask.get("aggregation", "min") not in ("min", "sum"):
            raise PackageError(f"subtask {subtask['index']}: aggregation must be "
                               f"'min' or 'sum'")
        for name in subtask["tests"]:
            if name not in available:
                raise PackageError(f"subtask {subtask['index']} references test "
                                   f"{name!r} but {name}.in does not exist.")
            if not (tests_dir / f"{name}.ans").exists():
                raise PackageError(f"test {name}.in has no matching {name}.ans")
            seen.add(name)

    orphans = sorted(available - seen)
    spec["_stats"] = {
        "test_count": len(seen),
        "subtask_count": len(subtasks),
        "max_score": sum(float(s["points"]) for s in subtasks),
        "orphan_tests": orphans,
    }
    return spec


def import_package(archive: Path | str, *, code: str | None = None,
                   replace: bool = True) -> "tuple":
    """
    Validate a package and install it under PROBLEM_ROOT.

    Returns (problem, warnings, notes). Warnings are things that may be
    wrong; notes are things that happened. Mixing them trains people to
    ignore both.
    """
    from .models import Problem  # local import: avoids app-loading order issues

    archive = Path(archive)
    staging = Path(tempfile.mkdtemp(prefix="oj-import-"))
    try:
        if archive.is_dir():
            source_root = archive
        else:
            with zipfile.ZipFile(archive) as zf:
                _safe_extract(zf, staging)
            source_root = staging
            # tolerate a single wrapping directory
            if not (source_root / "problem.json").exists():
                entries = [p for p in source_root.iterdir() if p.is_dir()]
                if len(entries) == 1 and (entries[0] / "problem.json").exists():
                    source_root = entries[0]

        spec = _validate(source_root)
        stats = spec.pop("_stats")
        problem_code = code or spec["code"]

        target = settings.PROBLEM_ROOT / problem_code
        # Importing a directory that already *is* the installed package would
        # otherwise delete the source and then fail to copy it. Found the
        # expensive way; the guard stays.
        if source_root.resolve() == target.resolve():
            pass
        else:
            if target.exists():
                if not replace:
                    raise PackageError(f"problem {problem_code!r} already exists.")
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_root, target)

        problem, _created = Problem.objects.update_or_create(
            code=problem_code,
            defaults={
                "title": spec.get("title", problem_code),
                "time_limit": float(spec.get("time_limit", 1.0)),
                "memory_limit_mb": int(spec.get("memory_limit_mb", 256)),
                "checker_type": spec.get("checker", {}).get("type", "token"),
                "max_score": stats["max_score"],
                "subtask_count": stats["subtask_count"],
                "test_count": stats["test_count"],
            },
        )
        warnings = []
        if stats["orphan_tests"]:
            warnings.append(
                f"{len(stats['orphan_tests'])} test file(s) exist but are not listed "
                f"in any subtask and will never run: "
                f"{', '.join(stats['orphan_tests'][:8])}"
                + (" ..." if len(stats["orphan_tests"]) > 8 else "")
            )
        if abs(stats["max_score"] - 100.0) > 1e-9:
            warnings.append(f"subtask points total {stats['max_score']:g}, not 100.")
        statement_warnings, notes = _import_statements(problem, target)
        warnings.extend(statement_warnings)
        return problem, warnings, notes
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _safe_extract(zf: zipfile.ZipFile, destination: Path) -> None:
    """Extract, refusing absolute paths and ../ traversal (zip-slip)."""
    destination = destination.resolve()
    for member in zf.infolist():
        target = (destination / member.filename).resolve()
        if not str(target).startswith(str(destination)):
            raise PackageError(f"refusing unsafe path in archive: {member.filename}")
    zf.extractall(destination)


# Recognised statement filenames inside a package. Language is taken from the
# suffix; a bare `statement.md` is assumed Greek, since that is what these are
# usually written in first.
_STATEMENT_PATTERNS = [
    ("statement.el.md", "el", "md"), ("statement.en.md", "en", "md"),
    ("statement.el.html", "el", "html"), ("statement.en.html", "en", "html"),
    ("statement.md", "el", "md"), ("statement.html", "el", "html"),
]
_PDF_PATTERNS = [("statement.el.pdf", "el"), ("statement.en.pdf", "en"),
                 ("statement.pdf", "el")]


def _import_statements(problem, package_dir: Path) -> list[str]:
    """
    Pick up statement bodies and PDFs shipped inside a package.

    An existing statement that a teacher has edited in the admin is never
    overwritten by a re-import: re-importing usually means the *tests*
    changed, and silently reverting someone's wording edits would be a nasty
    surprise. The warning says so explicitly.
    """
    from django.core.files import File

    from .models import Statement

    warnings: list[str] = []
    notes: list[str] = []
    seen: set[str] = set()

    for filename, language, body_format in _STATEMENT_PATTERNS:
        source = package_dir / filename
        if not source.exists() or language in seen:
            continue
        seen.add(language)
        existing = Statement.objects.filter(problem=problem, language=language).first()
        if existing and existing.body.strip():
            warnings.append(f"{filename} was not imported: a {language} "
                            f"statement already exists and your edits were kept.")
            continue
        Statement.objects.update_or_create(
            problem=problem, language=language,
            defaults={"body": source.read_text(encoding="utf-8-sig"),
                      "body_format": body_format,
                      "is_default": not Statement.objects.filter(
                          problem=problem, is_default=True).exclude(
                          language=language).exists()},
        )
        notes.append(f"imported {filename} as the {language} statement.")

    for filename, language in _PDF_PATTERNS:
        source = package_dir / filename
        if not source.exists():
            continue
        statement, _ = Statement.objects.get_or_create(
            problem=problem, language=language,
            defaults={"is_default": not Statement.objects.filter(
                problem=problem, is_default=True).exists()})
        if statement.pdf:
            continue
        with source.open("rb") as handle:
            statement.pdf.save(f"{language}.pdf", File(handle), save=True)
        notes.append(f"imported {filename}.")

    return warnings, notes
