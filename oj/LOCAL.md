# Running the site locally

Yes — everything works on your laptop with SQLite, no server, no Postgres, no
isolate. This is the same code that runs in production; only the environment
variables differ.

## One-time setup

In WSL2, from the folder that contains `judge/`, `web/` and `problems/`:

```bash
cd ~/oj
sudo apt install -y python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

The virtual environment keeps Django out of your system Python. You need
`source .venv/bin/activate` in every new terminal — if you see
`ModuleNotFoundError: No module named 'django'`, that's the step you skipped.

Then create the database and an account for yourself:

```bash
cd ~/oj/web
export OJ_SANDBOX=rlimit
python3 manage.py migrate
python3 manage.py createsuperuser
```

## Load the sample problem

```bash
python3 manage.py importproblem ../problems/sumsub --public
```

Expected: `sumsub: 2 subtask(s), 4 test(s), 100 points`.

## Start it

```bash
python3 manage.py runserver
```

Open <http://127.0.0.1:8000/>. Sign in with the superuser you just made,
open `sumsub`, paste this, and submit:

```cpp
#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;long long s=0,x;for(int i=0;i<n;i++){cin>>x;s+=x;}cout<<s<<"\n";}
```

You should land on a result page showing **AC, 100/100**, a green bar split
into two blocks — the narrow one is subtask 1 at 30 points, the wide one is
subtask 2 at 70 — and a table of all four tests.

Now change `long long s` to `int s` and submit again. The bar should come back
with the narrow block filled and the wide block empty: **PA, 30/100**. That
picture is the whole point of subtask scoring, and it's worth seeing once
before you design a marking scheme with it.

## Try the queue

The default grades inside the web request, which is fine for one person and
wrong for a class. To exercise the production path, in one terminal:

```bash
cd ~/oj/web && source ../.venv/bin/activate
export OJ_SANDBOX=rlimit OJ_JUDGE_INLINE=0
python3 manage.py runserver
```

and in a second terminal:

```bash
cd ~/oj/web && source ../.venv/bin/activate
export OJ_SANDBOX=rlimit
python3 manage.py runjudge
```

Submit again. The result page now appears as **QUEUED** and updates itself
when the worker picks it up — that polling behaviour is what students will
see during a contest. Stop the worker and submit again to watch a submission
sit in the queue; start it and watch it drain.

## Add your own problem

```bash
mkdir -p ~/oj/problems/mytask/tests
cp ~/oj/problems/sumsub/problem.json ~/oj/problems/mytask/problem.json
# edit the code, title, limits and subtasks
# add tests/01.in, tests/01.ans, ...
cd ~/oj/web && python3 manage.py importproblem ../problems/mytask --public
```

Or zip the folder's contents and upload it from **Admin → Problems → Upload
package**. Either route runs the same validation.

For a Greek statement, put `statement.html` in the package and save it as
UTF-8. The import reads it with `utf-8-sig`, so a BOM from a Windows editor
won't break it.

## Rehearse production, locally

Everything above uses `runserver`, which is a development tool. Before the
code goes near a server, run the *real* stack on your laptop:

```bash
cd ~/oj
bash scripts/local-prod.sh
```

That single command starts what production starts: `DEBUG` off, gunicorn
instead of `runserver`, collected static files, deployment checks, and two
judge workers draining a queue. It uses its own database
(`.local-prod/rehearsal.sqlite3`), so your development data is untouched.
Open <http://127.0.0.1:8010/>.

Create yourself an account in a second terminal:

```bash
cd ~/oj/web
OJ_DB_PATH=~/oj/.local-prod/rehearsal.sqlite3 ~/oj/.venv/bin/python manage.py createsuperuser
```

Then run a whole contest against yourself: create a contest in the admin
starting a minute out, add `sumsub` as problem A, register, wait for the
clock, submit, and watch the scoreboard. That rehearsal takes ten minutes and
is the cheapest way to find out that something about your contest settings
isn't what you assumed.

`bash scripts/local-prod.sh stop` stops it; `reset` deletes the rehearsal
database. Logs are in `.local-prod/`.

Three differences from the server, all deliberate: SQLite rather than
PostgreSQL, the `rlimit` sandbox rather than isolate, and no nginx (gunicorn
serves static files itself through WhiteNoise). Everything else — settings,
security checks, queue behaviour, graceful worker shutdown — is identical.

### Why the rehearsal turns two security settings off

`OJ_SSL_REDIRECT=0` and `OJ_SECURE_COOKIES=0` are set only in this script.
Both are on by default and must stay on with real TLS. Without them, on plain
HTTP, Django sends secure-only cookies that your browser will not send back,
and sign-in fails with no error message that explains why. This was found by
running the rehearsal rather than by reading the code, which is rather the
point of having one.

## Two things this local setup cannot tell you

1. **Memory limits.** The `rlimit` backend can't measure memory, so MLE is
   untestable until you're on a server with isolate.
2. **Whether it is safe.** The `rlimit` backend isolates nothing. Locally,
   only run code you wrote. Do not let students at this.

## Common stumbles

| Symptom | Cause |
|---|---|
| `No module named 'django'` | virtualenv not activated |
| `No module named 'judge'` | run from `~/oj/web`, not elsewhere; `manage.py` adds the parent |
| `No module named 'core'` | same |
| Submissions stay QUEUED | `OJ_JUDGE_INLINE=0` and no `runjudge` running |
| `This problem has no test data installed yet` | `importproblem` not run, or `OJ_PROBLEM_ROOT` points elsewhere |
| Greek text shows as `Î•Î»Î»Î·` | statement saved as Windows-1253; re-save as UTF-8 |
