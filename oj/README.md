# Online Judge — Phase 1: judging core

Sandboxed execution, subtask scoring, and offline problem validation. No web
layer yet; this is the piece everything else calls into.

## Try it

```bash
cd oj
OJ_SANDBOX=rlimit python3 -m judge.cli problems/sumsub solution.cpp
OJ_SANDBOX=rlimit python3 -m judge.cli problems/sumsub brute.py --expect PA
```

`OJ_SANDBOX=rlimit` forces the development backend. Leave it unset on the
server and `isolate` is picked automatically.

## The two sandboxes

| | `IsolateSandbox` | `RlimitSandbox` |
|---|---|---|
| isolation | namespaces, cgroups, seccomp | **none** |
| memory accounting | real, per-run cgroup | meaningless |
| use for | production | writing tasks on your laptop |

The dev backend exists so you can validate problem packages without a server.
It does not contain a hostile program. Never expose it to students.

## Problem package

```
problems/sumsub/
  problem.json
  tests/01.in  tests/01.ans  ...
  checker/checker.cpp        (only for checker type "custom")
```

```json
{
  "code": "sumsub",
  "time_limit": 1.0,
  "memory_limit_mb": 256,
  "checker": {"type": "token"},
  "subtasks": [
    {"index": 1, "points": 30, "aggregation": "min", "tests": ["01", "02"]},
    {"index": 2, "points": 70, "aggregation": "min", "tests": ["03", "04"]}
  ]
}
```

Omit `subtasks` entirely and you get one 100-point all-or-nothing subtask —
the ICPC case, with no special code path.

Checkers: `token` (default, whitespace-insensitive), `exact`, `float`
(with `epsilon`), `custom` (testlib-style, partial scores via stderr).

## Language limit scaling

Python gets 3× time and +64 MB by default. Override per problem:

```json
"language_overrides": {"py3": {"time_multiplier": 5.0, "memory_extra_mb": 128}}
```

## Feedback modes

`full` (practice), `subtask` (IOI contest — scores only), `first` (ICPC —
stops at first failure), `minimal`. Applied inside the worker, so hidden-test
detail never reaches the web tier in the first place.

## Server setup (when you get the VPS)

**If the host already runs CMS, do not install isolate** — `make install`
overwrites the binary CMS depends on. Reuse it instead:

```bash
export OJ_ISOLATE=/usr/local/bin/isolate
export OJ_BOX_OFFSET=100
```

Only on a host with no existing isolate:

```bash
apt install -y libcap-dev build-essential git g++ python3
git clone https://github.com/ioi/isolate && cd isolate && make install
isolate-check-environment          # must pass before you trust any timing
```

For reproducible time limits:

* dedicated (not burstable) vCPUs
* `isolcpus=2,3` on the kernel command line, judge workers pinned there
* one judge process per reserved core, one `box_id` each, never oversubscribed
* CPU governor set to `performance`

Sizing for ~150 students: 4 dedicated vCPU / 8–16 GB, 3 cores judging. A
20-test problem at 1 s is ~20 s of wall time per submission per core, so three
workers clear roughly 9 submissions/minute — comfortable for a class, and the
queue absorbs the submit-at-the-buzzer spike.

## Habit worth forming now

Keep the model solution, a brute force, and 2–3 deliberately-wrong solutions
next to each problem, each with its expected verdict, and run them all through
`--expect` before a contest. Subtask boundaries that fail to separate the
solutions you designed them to separate are the single most common way an
olympiad task turns out broken, and it is invisible until someone submits.

## Phase 2 — the site (done)

`web/` is a Django 5.2 project: accounts, problem pages, paste-or-upload
submission, result pages with a weighted subtask bar, admin package upload,
and a `runjudge` worker. See `LOCAL.md` to run it on your laptop and
`web/README.md` for configuration.

## Phase 3 — contests (done)

Timed windows, problem sets with labels, self-registration, IOI and ICPC
ranking, scoreboard freeze, per-contest feedback policy, unranked practice
after the end. Nine tests cover the ranking arithmetic:
`cd web && python3 manage.py test core`.

## Phase 4 — deployment (done)

`deploy/` holds gunicorn config, systemd units (web plus CPU-pinned workers
and a kill-switch target), an nginx site, an environment template, and a
backup script. `deploy/README.md` is the server runbook.

Rehearse it locally first — same settings, same server, same queue:

```bash
bash scripts/local-prod.sh
```

## Statements

Per problem, per language (Greek and English): Markdown, HTML, or PDF — or
text plus PDF together. Maths with `$…$` and `$$…$$`, rendered by KaTeX
vendored into the repo so nothing depends on outbound internet. Written in the
admin or shipped in the package as `statement.el.md` / `statement.en.pdf`.
See `web/README.md`.

## Frontend & security

The submit box is a real CodeMirror 6 editor (highlighting, line numbers,
JetBrains Mono, seed programs), vendored — no CDN. There is a dark theme with
a top-bar toggle that persists and follows the OS on first visit. Login
brute-force protection (django-axes), a per-hour submission cap, and
bleach-based statement sanitising round out the web-tier hardening. The judge
sandbox remains the primary security boundary.

## Documentation

| File | What it covers |
|---|---|
| `LOCAL.md` | running and rehearsing on your own machine |
| `DEPLOYMENT.md` | co-hosting with CMS, server survey, CPU reservation |
| `deploy/README.md` | the server install runbook |
| `web/README.md` | configuration, contests, problem packages |
