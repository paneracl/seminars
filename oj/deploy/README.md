# Server deployment

Run `bash scripts/local-prod.sh` and work through a full contest there before
starting this. Everything below assumes that rehearsal passed.

Read `../DEPLOYMENT.md` first if this host also runs CMS. The short version:
**do not install isolate**, reuse the one CMS has, and set `OJ_BOX_OFFSET=100`.

## 1. Account and layout

```bash
sudo adduser --system --group --home /srv/oj oj
sudo mkdir -p /srv/oj/{app,problems,static,media,backups} /etc/oj
sudo chown -R oj:oj /srv/oj
```

Everything the judge owns lives under `/srv/oj`; the only thing outside it is
`/etc/oj/oj.env`.

## 2. Code and virtualenv

```bash
sudo -u oj git clone <your-repo> /srv/oj/app     # or unzip the package there
sudo -u oj python3 -m venv /srv/oj/venv
sudo -u oj /srv/oj/venv/bin/pip install -r /srv/oj/app/requirements.txt
sudo -u oj /srv/oj/venv/bin/pip install "psycopg[binary]" gunicorn whitenoise
```

## 3. PostgreSQL

A separate database and role in the cluster CMS already uses. No interaction
with CMS's data.

```bash
sudo -u postgres createuser oj --pwprompt
sudo -u postgres createdb oj --owner=oj
```

## 4. Environment file

```bash
sudo cp /srv/oj/app/deploy/oj.env.example /etc/oj/oj.env
sudo chmod 600 /etc/oj/oj.env
sudo chown root:oj /etc/oj/oj.env
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # OJ_SECRET_KEY
sudo nano /etc/oj/oj.env
```

Check `OJ_TIME_ZONE` before a real round. Contest start and end times are
entered and displayed in it, and getting it wrong is the kind of mistake that
is only noticed by students staring at a locked problem page.

## 5. Database and static files

```bash
cd /srv/oj/app/web
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
  /srv/oj/venv/bin/python manage.py migrate && \
  /srv/oj/venv/bin/python manage.py collectstatic --no-input && \
  /srv/oj/venv/bin/python manage.py check --deploy && \
  /srv/oj/venv/bin/python manage.py createsuperuser'
```

`check --deploy` should report only the two HSTS-subdomain warnings. Anything
else, stop and fix it before going further.

## 6. Prove isolate works before wiring up services

```bash
cd /srv/oj
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
  /srv/oj/venv/bin/python -m judge.cli app/problems/sumsub /tmp/sol.cpp --expect AC'
```

Then the memory case, which the laptop rehearsal could not test:

```bash
cat > /tmp/mem.cpp <<'EOF'
#include <bits/stdc++.h>
int main(){std::vector<char> v; for(;;) v.resize(v.size()+50*1024*1024, 1);}
EOF
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
  /srv/oj/venv/bin/python -m judge.cli app/problems/sumsub /tmp/mem.cpp --expect MLE'
```

If that returns RE rather than MLE, cgroup memory accounting is not reaching
us. Fix it now, before students are seeing verdicts.

Then the full sandbox suite: 34 checks against the real isolate — every
verdict (AC, PA, WA, CE, RE, TLE, MLE, OLE) in C++ and Python, and a set of
hostile programs (reading host files, writing outside the box, network,
fork bomb, killing the judge, symlink tricks) that must all stay contained.
About a minute; it must end in `OK`:

```bash
cd /srv/oj/app
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; \
  /srv/oj/venv/bin/python -m unittest tests.test_isolate -v'
```

Re-run it after any isolate, kernel or compiler upgrade.

**Then submit a known-good solution through CMS** and confirm it still grades
normally. Do this on a quiet day, not the week of a round.

## 7. Services

```bash
sudo cp /srv/oj/app/deploy/oj-web.service /etc/systemd/system/
sudo cp /srv/oj/app/deploy/oj-worker@.service /etc/systemd/system/
sudo cp /srv/oj/app/deploy/oj-worker.target /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now oj-web
```

Start one worker per reserved core. If you reserved cores 2 and 3 with
`isolcpus=2,3`:

```bash
sudo systemctl enable --now oj-worker@2 oj-worker@3
sudo systemctl enable oj-worker.target
sudo systemctl status oj-worker@2
```

`oj-worker@N` pins itself to CPU N and uses sandbox `N + OJ_BOX_OFFSET`. Never
run more workers than reserved cores: oversubscription makes time limits
unreliable, which is the whole thing this design is trying to avoid.

## 8. nginx and TLS

Point a DNS A record at the server first, or certbot cannot verify anything.

```bash
sudo cp /srv/oj/app/deploy/nginx-judge.conf /etc/nginx/sites-available/judge
sudo nano /etc/nginx/sites-available/judge          # set your server_name
sudo ln -s ../sites-available/judge /etc/nginx/sites-enabled/judge
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d judge.cmscoinformatics.org
```

`nginx -t` before every reload. A syntax error in the new file takes down CMS
and Michanicos too, because they share the daemon.

## 9. Backups

```bash
sudo crontab -e
# 15 3 * * * /srv/oj/app/deploy/backup.sh >> /var/log/oj-backup.log 2>&1
```

The database holds accounts and submissions; `/srv/oj/problems` holds test
data you may have spent a weekend writing. The second one cannot be
reconstructed from anything. Once a term, actually restore a backup somewhere
and look at it — an untested backup is a hypothesis.

## 10. The kill switch

Add to your pre-round checklist, next to whatever you already do for CMS:

```bash
sudo systemctl stop oj-worker.target      # before a national round
sudo systemctl start oj-worker.target     # after
```

Queued submissions survive and grade when you start it again. A student
waiting an hour for a practice verdict is a non-event; a national round with
unreliable timings is not.

## Updating later

```bash
cd /srv/oj/app && sudo -u oj git pull
sudo -u oj bash -c 'set -a; . /etc/oj/oj.env; set +a; cd web; \
  /srv/oj/venv/bin/python manage.py migrate && \
  /srv/oj/venv/bin/python manage.py collectstatic --no-input'
sudo systemctl restart oj-web
sudo systemctl restart oj-worker.target
```

Never during a contest. `systemctl restart oj-worker.target` is safe mid-queue
— workers finish the submission in hand before exiting — but a migration
running while people submit is not.

## When something is wrong

```bash
sudo journalctl -u oj-web -n 100 --no-pager
sudo journalctl -u 'oj-worker@*' -n 100 --no-pager
sudo -u oj ls -la /var/local/lib/isolate/        # sandbox dirs; ours are 100+
```

| Symptom | Look at |
|---|---|
| Everything queued, nothing graded | is a worker running? `systemctl status oj-worker@2` |
| Every submission is `IE` | isolate: run the Part 6 checks again by hand |
| `MLE` never appears | cgroup memory accounting not reaching us |
| Verdicts inconsistent between runs | workers not pinned, or more workers than cores |
| CSS missing | `collectstatic` not run, or nginx `alias` path wrong |
| Sign-in silently fails | TLS not actually terminating; secure cookies never come back |
