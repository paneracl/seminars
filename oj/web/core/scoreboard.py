"""
Scoreboard computation.

Two ranking rules over one set of submissions:

  IOI   -- per problem, the best score achieved. Total is the sum. Ties are
           broken by *when* the competitor last improved their total: getting
           to 250 points in the first hour beats getting there in the last
           minute. This is the usual CMS convention.

  ICPC  -- a problem counts only when fully solved. Rank by number solved,
           then by penalty: minutes from the start of the contest to the
           accepted submission, plus `penalty_minutes` for each rejected
           attempt on that problem *before* the accepted one. Attempts on
           problems never solved cost nothing.

Freeze: for the last `freeze_minutes`, non-staff viewers see the standings as
they stood at the freeze moment. Submissions after it are shown only as a
pending count, so the room can see that something is happening without seeing
what. Staff always see the live board.

Compilation errors are, by convention, not penalised in ICPC scoring. They
usually mean the wrong file was pasted rather than a wrong idea, and
penalising them mostly punishes nerves.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.contrib.auth.models import User

from .models import Contest, ContestProblem, Participation, Submission

NOT_PENALISED = {"CE", "IE", ""}


@dataclass
class CellResult:
    label: str
    attempts: int = 0            # counted attempts (excludes CE/IE)
    pending: int = 0             # submissions hidden by the freeze
    solved: bool = False
    score: float = 0.0
    max_score: float = 0.0
    minutes: int | None = None   # ICPC: minute of the accepted submission
    penalty: int = 0             # ICPC: this problem's contribution

    @property
    def state(self) -> str:
        if self.solved:
            return "solved"
        if self.pending:
            return "pending"
        if self.score > 0:
            return "partial"
        if self.attempts:
            return "failed"
        return "none"


@dataclass
class Row:
    user: User
    cells: dict[str, CellResult] = field(default_factory=dict)
    total: float = 0.0
    solved: int = 0
    penalty: int = 0
    last_improvement: int = 0    # seconds from start; IOI tie-break
    rank: int | None = None
    ordered_cells: list = field(default_factory=list)

    @property
    def display_total(self) -> str:
        return f"{self.total:g}"


def build(contest: Contest, *, viewer=None) -> dict:
    """Compute the scoreboard. Returns a dict ready for the template."""
    staff_view = bool(viewer is not None and getattr(viewer, "is_staff", False))
    frozen = contest.is_frozen and not staff_view
    freeze_at = contest.freeze_at if frozen else None

    contest_problems = list(
        ContestProblem.objects.filter(contest=contest).select_related("problem"))
    labels = [cp.label for cp in contest_problems]
    problem_label = {cp.problem_id: cp.label for cp in contest_problems}
    problem_max = {cp.label: cp.problem.max_score for cp in contest_problems}

    participations = (Participation.objects
                      .filter(contest=contest)
                      .select_related("user"))
    if not staff_view:
        participations = participations.filter(is_hidden=False)
    rows = {p.user_id: Row(user=p.user,
                           cells={label: CellResult(label=label, max_score=problem_max[label])
                                  for label in labels})
            for p in participations}

    submissions = (Submission.objects
                   .filter(contest=contest,
                           counts_for_ranking=True,
                           user_id__in=rows.keys(),
                           problem_id__in=problem_label.keys())
                   .order_by("created_at"))

    for submission in submissions:
        row = rows[submission.user_id]
        cell = row.cells[problem_label[submission.problem_id]]

        if freeze_at is not None and submission.created_at >= freeze_at:
            cell.pending += 1
            continue
        if submission.status != Submission.Status.DONE:
            cell.pending += 1
            continue

        seconds = int((submission.created_at - contest.start_time).total_seconds())

        if contest.scoring == Contest.Scoring.ICPC:
            if cell.solved:
                continue                      # further attempts are irrelevant
            if submission.verdict in NOT_PENALISED:
                continue
            if submission.verdict == "AC":
                cell.solved = True
                cell.score = submission.max_score
                cell.minutes = seconds // 60
                cell.penalty = cell.minutes + contest.penalty_minutes * cell.attempts
                row.last_improvement = max(row.last_improvement, seconds)
            else:
                cell.attempts += 1
        else:
            if submission.verdict in ("IE",):
                continue
            cell.attempts += 1
            if submission.score > cell.score:
                cell.score = submission.score
                row.last_improvement = max(row.last_improvement, seconds)
            if submission.verdict == "AC":
                cell.solved = True

    for row in rows.values():
        row.ordered_cells = [row.cells[label] for label in labels]
        if contest.scoring == Contest.Scoring.ICPC:
            row.solved = sum(1 for c in row.cells.values() if c.solved)
            row.penalty = sum(c.penalty for c in row.cells.values() if c.solved)
            row.total = row.solved
        else:
            row.total = round(sum(c.score for c in row.cells.values()), 6)

    ordered = sorted(
        rows.values(),
        key=(lambda r: (-r.solved, r.penalty, r.last_improvement, r.user.username))
        if contest.scoring == Contest.Scoring.ICPC else
        (lambda r: (-r.total, r.last_improvement, r.user.username)),
    )

    # Equal results share a rank, and the next distinct result skips ahead.
    previous_key = None
    for position, row in enumerate(ordered, start=1):
        key = ((row.solved, row.penalty) if contest.scoring == Contest.Scoring.ICPC
               else (row.total,))
        if key == previous_key:
            row.rank = ordered[position - 2].rank
        else:
            row.rank = position
        previous_key = key

    return {
        "contest": contest,
        "contest_problems": contest_problems,
        "labels": labels,
        "rows": ordered,
        "frozen": frozen,
        "freeze_at": contest.freeze_at,
        "is_icpc": contest.scoring == Contest.Scoring.ICPC,
    }
