# Online Judge — Install & Teacher Guide

---

# Part 1 — Installing on a Linux server

Full detail lives in `deploy/README.md` in the project. This is the
condensed path. Rehearse with `bash scripts/local-prod.sh` on your own
machine before touching a server — see `LOCAL.md`.

**If this server already runs CMS**, read `DEPLOYMENT.md` first. The one
rule that matters more than the rest: **do not run `make install` from an
isolate checkout** — it overwrites the binary CMS depends on. Reuse it via
`OJ_ISOLATE=/usr/local/bin/isolate` and set `OJ_BOX_OFFSET=100` so sandbox
IDs never collide with CMS's.

### 1. Account and layout

```bash
sudo adduser --system --group --home /srv/oj oj
sudo mkdir -p /srv/oj/{app,problems,static,media,backups} /etc/oj
sudo chown -R oj:oj /srv/oj
```

### 2. Code and virtualenv

```bash
sudo -u oj git clone <your-repo> /srv/oj/app     # or unzip the package there
sudo -u oj python3 -m venv /srv/oj/venv
sudo -u oj /srv/oj/venv/bin/pip install -r /srv/oj/app/requirements.txt
sudo -u oj /srv/oj/venv/bin/pip install "psycopg[binary]" gunicorn whitenoise
```

### 3. PostgreSQL

A separate database and role in the cluster CMS already uses.

```bash
sudo -u postgres createuser oj --pwprompt
sudo -u postgres createdb oj --owner=oj
```

### 4. Environment file

```bash
sudo cp /srv/oj/app/deploy/oj.env.example /etc/oj/oj.env
sudo chmod 600 /etc/oj/oj.env
sudo chown root:oj /etc/oj/oj.env
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # → OJ_SECRET_KEY
sudo nano /etc/oj/oj.env
```

Double check `OJ_TIME_ZONE` — contest windows are entered and shown in it.

### 5. Database, static files, superuser

```bash
cd /srv/oj/app/web
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
  /srv/oj/venv/bin/python manage.py migrate && \
  /srv/oj/venv/bin/python manage.py collectstatic --no-input && \
  /srv/oj/venv/bin/python manage.py check --deploy && \
  /srv/oj/venv/bin/python manage.py createsuperuser'
```

`check --deploy` should report only two HSTS-subdomain warnings. Anything
else, stop and fix it first.

### 6. Prove isolate works before wiring up services

```bash
cd /srv/oj
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
  /srv/oj/venv/bin/python -m judge.cli app/problems/sumsub /tmp/sol.cpp --expect AC'
```

Then the memory case (untestable on a laptop):

```bash
cat > /tmp/mem.cpp <<'EOF'
#include <bits/stdc++.h>
int main(){std::vector<char> v; for(;;) v.resize(v.size()+50*1024*1024, 1);}
EOF
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
  /srv/oj/venv/bin/python -m judge.cli app/problems/sumsub /tmp/mem.cpp --expect MLE'
```

RE instead of MLE means cgroup memory accounting isn't reaching us — fix
before students see verdicts. **Then submit a known-good solution through
CMS itself** and confirm it still grades normally, on a quiet day.

### 7. Services

```bash
sudo cp /srv/oj/app/deploy/oj-web.service /etc/systemd/system/
sudo cp /srv/oj/app/deploy/oj-worker@.service /etc/systemd/system/
sudo cp /srv/oj/app/deploy/oj-worker.target /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oj-web
```

One worker per reserved CPU core. If you reserved cores 2 and 3:

```bash
sudo systemctl enable --now oj-worker@2 oj-worker@3
sudo systemctl enable oj-worker.target
```

Never run more workers than reserved cores — oversubscription makes time
limits unreliable.

### 8. nginx and TLS

Point DNS at the server first.

```bash
sudo cp /srv/oj/app/deploy/nginx-judge.conf /etc/nginx/sites-available/judge
sudo nano /etc/nginx/sites-available/judge          # set server_name
sudo ln -s ../sites-available/judge /etc/nginx/sites-enabled/judge
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d judge.cmscoinformatics.org
```

`nginx -t` before every reload — a bad config here takes down CMS and
Michanicos too, since they share the daemon.

### 9. Backups

```bash
sudo crontab -e
# 15 3 * * * /srv/oj/app/deploy/backup.sh >> /var/log/oj-backup.log 2>&1
```

Test a restore once a term. An untested backup is a hypothesis.

### 10. The kill switch

Add to your pre-round checklist:

```bash
sudo systemctl stop oj-worker.target      # before a national round
sudo systemctl start oj-worker.target     # after
```

Queued submissions survive and grade when restarted.

### If something's wrong

```bash
sudo journalctl -u oj-web -n 100 --no-pager
sudo journalctl -u 'oj-worker@*' -n 100 --no-pager
```

| Symptom | Check |
|---|---|
| Everything queued, nothing graded | `systemctl status oj-worker@2` |
| Every submission is `IE` | re-run the Part 6 isolate checks |
| `MLE` never appears | cgroup memory accounting not reaching us |
| Verdicts inconsistent between runs | workers not pinned, or over-subscribed |
| CSS missing | `collectstatic` not run, or nginx `alias` path wrong |
| Sign-in silently fails | TLS not actually terminating |

---

# Part 2 — Guide for teachers

## Who can do this

Two levels of access:

- **Staff** (`is_staff`) — can sign into `/admin/`, upload problems, create
  contests, view all submissions, rejudge. This is the level to give
  teachers.
- **Superuser** (`is_superuser`) — staff plus unrestricted power over every
  setting, including other people's accounts. Keep this to one or two
  accounts.

To make someone staff: **Admin → Users → (their account) → tick "Staff
status" → Save.** No superuser needed for the day-to-day tasks below —
regular staff status is enough, and it's revocable per person.

Sign in at `https://judge.cmscoinformatics.org/admin/` (or
`http://127.0.0.1:8010/admin/` when testing locally).

## What students see

Worth knowing, so you can answer questions:

- **A real code editor.** The submission box has syntax highlighting, line
  numbers, and bracket matching, in JetBrains Mono. It opens pre-filled with a
  small skeleton for the chosen language; switching the language dropdown
  swaps the skeleton only if the student hasn't started typing, so it never
  erases their work. They can also upload a `.cpp` or `.py` file instead.
- **A dark/light toggle** (sun/moon, top-right of every page). The choice is
  remembered per browser and defaults to the student's system setting.
- **A "Run code" button.** Before submitting, a student can compile and run
  their program and see the output — handy for catching compile errors and
  obvious bugs without spending a submission. The input box starts prefilled
  with the sample input, so pressing Run straight away gives a meaningful
  result; they can edit or clear it to test their own cases. It is not graded
  (it runs on that input, not the hidden tests) and it has its own tight rate
  limit, separate from submissions. If a problem's first test is secret, set
  `"hide_sample_input": true` in its `problem.json` so nothing is prefilled.
- **Live results.** After submitting, the result page updates itself when the
  verdict is ready — no need to refresh. How much detail they see (full tests,
  subtask scores only, verdict alone) depends on the contest's feedback
  setting.

## Adding a problem

**Admin → Problems → Upload package** (top-right button on the problem
list). Upload a `.zip` containing:

```
problem.json
tests/01.in   tests/01.ans   tests/02.in   tests/02.ans  …
statement.el.md          (optional — Greek, Markdown)
statement.en.md          (optional — English, Markdown)
statement.el.pdf         (optional — Greek, PDF)
checker/checker.cpp      (optional — only for custom checkers)
```

The import **validates before writing anything** — it refuses a package
whose subtasks reference a test file that doesn't exist, so a broken
package is caught here rather than mid-contest as a mysterious error on a
student's submission.

After upload you land on the problem's edit page. **The problem stays
invisible to students until you tick "is public"** — check statements and
sample tests first.

### Editing tests, subtasks and statements in the browser

You do not have to build a zip. Once a problem exists, its admin page has three
tools (top-right): **Manage statements**, **Manage tests**, and **Manage
subtasks**.

- **Manage tests** — each test is a card with its input and expected output
  side by side, plus checkboxes for which subtask(s) it belongs to. Add, edit,
  rename, or delete tests directly. Saving re-validates the whole package, so
  you cannot save something the judge would choke on.
- **Manage subtasks** — set points and aggregation per subtask; the total is
  computed for you. Test memberships are preserved when you renumber.
- **Manage statements** — a Markdown editor with a formatting toolbar and a
  **Preview** tab that shows exactly what students will see, maths included.
  One card per language; tick "Show this one first" to set the default.

Uploading a zip still works and is handy for bulk import, but for day-to-day
authoring the in-browser editors are usually faster.

### Writing a statement

Two ways: ship `statement.el.md` / `statement.en.md` inside the package, or
write directly in the admin under the problem's **Statements** section.
Editing there later is never overwritten by a re-import — re-importing
usually means the *tests* changed, and a re-import would otherwise silently
discard your wording edits.

Markdown, with maths in `$...$` and `$$...$$`:

```markdown
Δίνεται ακολουθία $n$ ακεραίων με $1 \le n \le 10^5$.

$$S = \sum_{i=1}^{n} a_i$$

| Είσοδος | Έξοδος |
|---------|--------|
| `3`<br>`1 2 3` | `6` |
```

A PDF can be attached instead of, or alongside, the text — useful when a
statement came from LaTeX or needs figures. Students see a language switcher
automatically when more than one is present.

### Before making it public

Keep a model solution and a couple of deliberately-wrong ones (overflow,
off-by-one) and run them from a terminal to confirm the subtasks actually
separate what they're meant to:

```bash
cd ~/oj
python3 -m judge.cli problems/mytask solutions/model.cpp --expect AC
python3 -m judge.cli problems/mytask solutions/overflow.cpp --expect PA
```

This catches the single most common way a task turns out broken, and it's
invisible until a student hits it.

## Creating a contest

**Admin → Contests → Add contest.**

| Field | What it does |
|---|---|
| Start / end time | The window. Shown in the server's local timezone. |
| Scoring | **IOI** — best score per problem, summed, ties broken by who got there first. **ICPC** — problems solved, then penalty minutes. |
| Freeze minutes | Freezes the *public* scoreboard for the closing stretch (0 = never). You still see it live as staff. |
| Feedback | What competitors see while it's running: full detail, subtask scores only, first failure only, or verdict alone. |
| Reveal after end | Opens full detail once the contest finishes — no rejudge needed. |
| Is listed | Off = reachable only by direct link (good for a dry run). |
| Open registration | On = students register themselves from the contest page. |
| Practice after end | Lets submissions keep running afterwards, recorded as unranked so a finished scoreboard can never change. |

On the same page: the **Contest problems** section — add each problem and
give it a label, `A`, `B`, `C`. And **Participations**, if you'd rather
pre-register a class yourself than have students sign up.

### The one setting worth double-checking

**Leave contest problems' "is public" flag off until the contest ends.**
Public access and contest access are separate gates — a public problem is
readable at `/problems/<code>/` immediately, regardless of the contest
window. Tick it afterwards to move the problem into the open practice
archive.

### Worth doing once before a real round

Create a contest starting two minutes out, add one problem, register as a
student would, and watch it unseal. If you're using a freeze, set it longer
than the time remaining and load the scoreboard as both a student and
yourself, so you've seen both views before anyone else has.

## Day-to-day

- **Rejudge a submission** — Admin → Submissions → select rows → action
  "Rejudge selected submissions". Useful after fixing a broken checker.
- **See everyone's submissions** — Admin → Submissions, or `/submissions/`
  on the site itself while signed in as staff.
- **A student is locked out of login** — after 8 failed password attempts an
  account is temporarily locked (a brute-force guard). It clears itself after
  15 minutes, or you can clear it immediately from the server:

  ```
  cd /srv/oj/app/web
  sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
    /srv/oj/venv/bin/python manage.py axes_reset'
  ```

  `axes_reset` clears all locks; `axes_reset_username THENAME` clears one.
- **A student hit the submission limit** — there is a cap of 60 submissions
  per hour per student (staff are exempt). It resets on its own; it exists to
  stop a runaway script from flooding the judge, not to limit normal work.
- **The kill switch is server-level**, not something available from the
  website — see Part 1, step 10.

## Reference

- `LOCAL.md` — running and rehearsing on your own machine
- `web/README.md` — configuration, contest fields, statement format in
  full
- `DEPLOYMENT.md` — co-hosting with CMS, server survey
- `deploy/README.md` — the full server install runbook
