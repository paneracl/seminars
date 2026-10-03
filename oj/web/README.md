# Web layer (Phase 2)

Django 5.2 LTS. SQLite locally, PostgreSQL in production, same code either way.

## What exists

* accounts: register, sign in, sign out
* problems: list, statement page, per-user best score
* submissions: paste into a textarea or upload a `.cpp`/`.py` file
* results: verdict, weighted subtask bar, per-test table, compiler output
* admin: upload a problem package as a zip, validated before install; rejudge
* `runjudge`: the worker, one per reserved CPU core
* contests: timed window, problem set, self-registration, IOI and ICPC
  ranking, scoreboard freeze, feedback policy, post-contest practice

## Contests

A contest is a window, a problem set, and two rules: how to rank, and how much
to reveal.

**Ranking.** IOI ranks by total score (best per problem, summed), ties broken
by who reached that total first. ICPC ranks by problems solved, then penalty:
minutes to the accepted submission plus 20 per rejected attempt *before* it.
Attempts on unsolved problems cost nothing, and compilation errors are not
counted as attempts — they usually mean the wrong file was pasted, and
penalising them punishes nerves rather than wrong ideas.

**Reveal.** `feedback` controls what competitors see while the contest runs:
full tests, subtask scores only, verdict plus first failing test, or verdict
alone. Judging always records everything; the policy is applied when the page
is rendered, so `reveal_after_end` can open it all up afterwards with no
rejudge. Staff always see the full picture.

**Freeze.** `freeze_minutes` freezes the public scoreboard for the closing
stretch. Submissions after it show as a pending marker: the room can see that
something happened without seeing whether it worked.

**After the end.** With `practice_after_end`, submissions still run but are
recorded as unranked, so a finished scoreboard can never change.

Registration matters: a submission counts only if the competitor registered
*before* submitting. Staff registrations default to hidden, so a teacher
testing the problems does not appear in the standings.

Run the scoreboard tests with `python3 manage.py test core`.

## Two ways to grade

`OJ_JUDGE_INLINE=1` (the default when `OJ_DEBUG=1`) grades inside the web
request. Convenient for one person testing; the request blocks for the full
runtime of every test, so it is unusable with a class.

`OJ_JUDGE_INLINE=0` puts submissions in a queue and a separate `runjudge`
process drains it. That is what production uses, and it is worth running
locally at least once so you have seen both paths work.

The queue is a database table rather than Redis or Celery. At 150 students
the throughput is trivial, and a dependency you don't have is a dependency
that can't fail the week of a contest. Multiple workers coordinate with
`SELECT ... FOR UPDATE SKIP LOCKED` on PostgreSQL.

## Environment variables

| Variable | Default | Notes |
|---|---|---|
| `OJ_DEBUG` | `1` | set `0` in production |
| `OJ_SECRET_KEY` | — | required when `OJ_DEBUG=0` |
| `OJ_ALLOWED_HOSTS` | — | required when `OJ_DEBUG=0`; comma-separated |
| `OJ_DB` | `sqlite` | `postgres` in production |
| `OJ_PROBLEM_ROOT` | `../problems` | where packages are installed |
| `OJ_JUDGE_INLINE` | follows `OJ_DEBUG` | grade in-request |
| `OJ_SANDBOX` | auto | `rlimit` locally, `isolate` on the server |
| `OJ_ISOLATE` | `isolate` | path to the binary CMS already installed |
| `OJ_BOX_OFFSET` | `0` | set to `100` on a host shared with CMS |
| `OJ_SUBMIT_COOLDOWN` | `10` | seconds between submissions per user |

## The submit editor

The submission box is CodeMirror 6: syntax highlighting, line numbers, bracket
matching, and real tab handling, with JetBrains Mono. It is bundled into a
single vendored file (`static/vendor/codemirror/`) — no CDN, no runtime build
step, consistent with everything else here.

It enhances a plain `<textarea>` rather than replacing it: if the script fails
to load, the box still works and the form still submits. A fresh box opens
with a language-appropriate skeleton (the C++ / Python seed programs live on
each `Language` in `judge/languages.py`); switching language swaps the
skeleton only if you have not started typing, so it never discards real work.

## Dark theme

Sun/moon toggle, top-right. The choice persists in `localStorage`; first-time
visitors follow their OS `prefers-color-scheme`. A tiny script sets the theme
before first paint, so there is no flash of light UI on load. It is one extra
CSS-variable block, not a second stylesheet — every rule reads those tokens,
so the whole UI (including the scoreboard's colour-coded cells and the
CodeMirror theme) re-themes from one place.

## Run Code

A HackerRank-style "Run code" button under the editor compiles and runs the
student's program on custom input they type, ungraded, and shows stdout /
stderr / status. It reuses the exact judging sandbox and language limits via
`judge.run_once`, so behaviour matches grading. Because it runs untrusted code
on demand outside the queue, it has its own limits, separate from submissions
and staff-exempt: `RUN_PER_MINUTE` (default 6) and `RUN_HOURLY_CAP` (default
80), plus `MAX_RUN_INPUT_BYTES` (default 64 KB) on the custom input. Inside a
contest the run endpoint is gated by the same seal as viewing the problem, so
a sealed problem cannot be run before it opens.

When the custom-input box is left empty, Run Code uses the problem's sample input: an explicit `sample_input` string in `problem.json` if present, otherwise the first test's input. A problem whose first test is secret can set `"hide_sample_input": true` to suppress this, in which case the box is not prefilled and an empty run feeds no input.

## Security

- **Login brute-force protection** via django-axes: lockout after
  `OJ_LOGIN_FAILURE_LIMIT` failures (default 8) per *username + IP*, for
  `OJ_LOGIN_COOLOFF_HOURS` (default 15 min). Per-pair lockout means one
  attacker cannot lock every student out by guessing usernames.
- **Submission flood cap**: `OJ_SUBMIT_HOURLY_CAP` (default 60) submissions
  per user per rolling hour, on top of the existing per-submission cooldown.
  Staff are exempt. This stops a script or a stuck loop from swamping the
  judge queue mid-contest.
- **Statement HTML** is sanitised with `bleach`, a maintained library, rather
  than a hand-rolled parser.
- The judge sandbox (isolate, resource caps, no network) remains the primary
  security boundary — see `DEPLOYMENT.md`.

## Statements

Each problem can have one statement per language (Greek and English), and each
statement can be **Markdown**, **HTML**, or a **PDF** — or text plus a PDF
together, which suits a printed contest with online practice afterwards.

Markdown is the default because these are typed by hand: it is quicker to
write, searchable, diffable in git, and students can copy a sample input out
of it. Reach for PDF when the statement came out of LaTeX or has figures.

Maths uses `$inline$` and `$$display$$` and is rendered by **KaTeX, vendored
into the repository** rather than loaded from a CDN — a school LAN may have no
outbound internet, and a statement whose maths silently fails to render is
worse than one with no maths at all. Maths spans are lifted out before the
Markdown pass so `$a_i \le 10^5$` is not mangled into emphasis.

Statement HTML is filtered to a known-good tag set. Teachers are not the
threat model; pasting from a website that carried a script tag is.

PDFs are served by a Django view, never from `/static/`. A static path would
let anyone who guessed the URL read a contest statement before the contest
opened.

Edit statements in the admin under each problem, or ship them in the package:

## Problem package format

```
problem.json
tests/01.in  tests/01.ans  ...
statement.el.md      (optional; also .en.md, .el.html, .en.html)
statement.el.pdf     (optional; also .en.pdf)
checker/checker.cpp  (optional; only for "checker": {"type": "custom"})
```

A re-import never overwrites a statement someone has edited in the admin —
re-importing usually means the *tests* changed, and silently reverting
somebody's wording would be a nasty surprise. It warns instead.

Import from the admin (Problems → Upload package) or the command line:

```bash
python3 manage.py importproblem /path/to/package.zip --public
```

Import validates first and refuses a package whose subtasks reference a test
file that doesn't exist. That check exists because the alternative is finding
out mid-contest, as a Judge Error on a student's submission.
