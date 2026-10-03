# Running the judge: locally now, on your servers later

Two separate things, and it helps to keep them separate in your head:

1. **Testing locally today.** The judging core works right now. You can grade
   real submissions against real problem packages on your own machine this
   afternoon. No server involved.
2. **Deploying the platform.** The web layer (registration, editor, contests)
   is Phase 2 and doesn't exist yet. But the *server preparation* — deciding
   where it lives, proving the sandbox works there, reserving CPU — can and
   should happen first, because that's where the risk is.

---

# Part A — Testing locally

## A.1 If you're on Windows

Use WSL2. The judge is Linux-only by nature: it depends on cgroups, POSIX
resource limits and process groups, none of which have Windows equivalents.

```powershell
wsl --install -d Ubuntu-24.04
```

Reboot when prompted, then open the Ubuntu terminal and set a UNIX username.
Verify you got WSL2, not WSL1:

```powershell
wsl -l -v          # VERSION column must say 2
```

WSL1 shares the Windows kernel and will not give you working resource limits.
If it says 1: `wsl --set-version Ubuntu-24.04 2`.

## A.2 Install the toolchain

```bash
sudo apt update
sudo apt install -y g++ python3 python3-pip git
g++ --version && python3 --version
```

## A.3 Get the code into the Linux filesystem

**Put it in `~`, not under `/mnt/c/`.** Files on the Windows drive are reached
through a translation layer that is roughly ten times slower and does not
carry UNIX permissions properly. Compilation and test I/O will crawl, and the
executable bit on compiled binaries behaves unpredictably.

```bash
mkdir -p ~/oj && cd ~/oj
# copy the oj/ folder here, e.g.:
cp -r /mnt/c/Users/YOURNAME/Downloads/oj/* .
ls        # expect: README.md  judge/  problems/  scripts/
```

## A.4 Run it

```bash
cd ~/oj
cat > /tmp/sol.cpp <<'EOF'
#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;long long s=0,x;for(int i=0;i<n;i++){cin>>x;s+=x;}cout<<s<<"\n";}
EOF

OJ_SANDBOX=rlimit python3 -m judge.cli problems/sumsub /tmp/sol.cpp
```

Expected: `AC   100/100 points`.

Now break it deliberately — change `long long s` to `int s` and rerun. You
should get `PA 30/100`, subtask 1 accepted, subtask 2 failing with the actual
wrong number printed. That's the thing worth confirming: subtask boundaries
separate solutions the way you designed them to.

```bash
OJ_SANDBOX=rlimit python3 -m judge.cli problems/sumsub /tmp/sol.cpp --expect PA
echo $?     # 0 if the verdict matched, 1 if not
```

## A.5 What `OJ_SANDBOX=rlimit` means, and why it matters

`rlimit` is the development backend. It enforces CPU time, address space and
output size, and nothing else. **It does not isolate anything** — no
filesystem restriction, no network restriction, no reliable containment of a
program that actively tries to escape.

That is fine for what you'll use it for: validating your own problem packages
and your own solutions. It is not fine for student submissions. Locally, only
ever run code you wrote yourself.

It also cannot measure memory. The number it prints is a high-water mark
across every child process, including the compiler, so MLE verdicts from local
runs mean nothing. Memory limits are only real under `isolate`.

## A.6 Making your own problem package

```
problems/myproblem/
  problem.json
  tests/01.in   tests/01.ans
  tests/02.in   tests/02.ans
```

Copy `problems/sumsub/problem.json` and edit. The habit worth building:
alongside each problem keep a `solutions/` folder with the model solution, a
brute force, and two or three deliberately-wrong ones, each with its intended
verdict, and run all of them before the task goes near students.

---

# Part B — Surveying your servers

I can't see your machines, so before giving you exact commands I need to know
what's actually on them. `scripts/survey.sh` collects that. It is read-only:
it runs no installer, touches no CMS configuration, and does not read CMS's
database.

```bash
bash scripts/survey.sh > survey-$(hostname).txt 2>&1
```

Run it on **both** servers and send me the two files. The parts that determine
everything else:

| What it reports | Why it decides something |
|---|---|
| `isolate --version` | 1.x and 2.x take different flags. Version mismatch is the main way a second judge breaks CMS. |
| existing box dirs in `/var/local/lib/isolate` | tells us which sandbox IDs CMS is already using |
| `nproc`, `Hypervisor vendor` | whether there are cores to spare, and whether vCPUs are dedicated |
| `systemd-detect-virt` | if it says `lxc` or `openvz`, isolate cannot work there at all |
| cgroup version | v2 is required by modern isolate |
| listening ports | which of 8888/8889/8890 CMS is holding |
| nginx `server_name`s | how the two existing sites are routed, so a third slots in cleanly |

---

# Part C — Can it share a box with CMS? The honest answer

Technically yes. But there are three real conflicts, and one of them is
serious enough to shape the whole decision.

## C.1 The serious one: CPU contention makes time limits lie

CMS grades by measuring CPU time inside a sandbox. So does this judge. If both
are grading at the same moment on the same cores, both sets of measurements
drift — cache pressure, memory bandwidth contention, scheduler interference.
A solution that takes 0.94 s alone can take 1.15 s under load and get TLE.

For a school practice judge that's an annoyance. **For your national olympiad
rounds it is a correctness problem**, and it would be caused by the school
judge, on your contest server, during the contest.

This is the argument for a specific arrangement rather than against
co-hosting entirely:

- **Do not put the school judge on the CMS contest server.** That machine's
  job is to be boringly reliable a few days a year.
- **Michanicos' server is the reasonable host** — it's a training platform, so
  it already accepts that timings are indicative rather than sacred, and its
  load profile is compatible.
- **Reserve separate cores anyway** (Part D.3), so the two judges are not
  fighting over the same physical CPUs.
- **Add a kill switch** (Part E) that stops school judging during national
  rounds, in case both platforms ever end up on one machine.

If neither server has spare dedicated cores, a €25/month VPS for the school
judge is cheaper than the afternoon you'd spend investigating why a national
contest produced inconsistent TLEs. I'd rather tell you that now than after.

## C.2 The dangerous one: do not install a second isolate

CMS ships with and depends on a specific isolate version. Running
`make install` from an isolate checkout writes to `/usr/local/bin/isolate` and
**will overwrite CMS's binary**. If the versions differ in flag handling or
meta-file format, CMS's workers start failing — possibly not immediately, and
possibly first noticed during a contest.

So: **reuse the isolate that's already there.** The code supports this
directly:

```bash
export OJ_ISOLATE=/usr/local/bin/isolate     # whatever survey.sh found
```

`IsolateSandbox` detects the major version at startup and adjusts its flags
(1.x needs `--cg`; 2.x always uses cgroups and rejects the flag). Nothing gets
installed, nothing gets overwritten.

## C.3 The easy one: sandbox ID collisions

isolate boxes are numbered. CMS uses low numbers starting at 0. If our judge
also initialises box 0 while a CMS worker is using it, the two processes share
a working directory mid-run and both produce garbage verdicts — silently, with
no error.

Fix is one environment variable:

```bash
export OJ_BOX_OFFSET=100     # our boxes become 100, 101, 102...
```

Check `survey.sh`'s box-directory listing to confirm 100+ is clear.

## C.4 The trivial ones

Postgres: create a separate database and role in the existing cluster. No
interaction with CMS's data. Nginx: a new `server` block on a new subdomain
(`judge.cmscoinformatics.org`), a DNS A record, and certbot. Redis: a separate
database number, or a second instance on another port.

---

# Part D — Server preparation you can do now

These steps are useful regardless of what Phase 2 looks like, and they prove
the risky part works before there's a web app depending on it.

## D.1 Confirm isolate works for us

```bash
sudo apt install -y g++ python3 git
mkdir -p ~/oj && cd ~/oj          # copy the code here

export OJ_ISOLATE=/usr/local/bin/isolate
export OJ_BOX_OFFSET=100
python3 -m judge.cli problems/sumsub /tmp/sol.cpp --expect AC
```

If that prints AC, isolate is working for us without touching CMS. Then run
the deliberately-wrong solutions and confirm PA and TLE, and — the one the
local backend can't test — a memory bomb:

```bash
cat > /tmp/mem.cpp <<'EOF'
#include <bits/stdc++.h>
int main(){std::vector<char> v; for(;;) v.resize(v.size()+50*1024*1024, 1);}
EOF
python3 -m judge.cli problems/sumsub /tmp/mem.cpp --expect MLE
```

If that returns RE instead of MLE, cgroup memory accounting isn't reaching us
and needs fixing before students see verdicts.

## D.2 Verify you didn't disturb CMS

Immediately after: submit a known-good solution through CMS's own admin
interface and confirm it grades normally. Do this while nobody is using the
system, not the week of a round.

## D.3 Reserve cores

Give the school judge its own cores so it can't perturb the neighbour. On a
4-core box, reserve core 3:

```
# /etc/default/grub -> GRUB_CMDLINE_LINUX_DEFAULT="... isolcpus=3 nohz_full=3"
sudo update-grub && sudo reboot
```

Then pin the judge worker to it (in the systemd unit, Phase 2):

```
CPUAffinity=3
```

And set the governor so clock speed doesn't wander mid-measurement:

```bash
sudo apt install -y linux-tools-common linux-tools-$(uname -r)
sudo cpupower frequency-set -g performance
```

One judge process per reserved core, one `box_id` each, never more processes
than cores. Oversubscription is the other way to make time limits lie.

---

# Part E — The kill switch

Before students ever use it, have a way to stop the school judge instantly:

```bash
sudo systemctl stop oj-worker.target     # Phase 2 unit
```

Put that in your pre-round checklist next to whatever you already do for CMS.
Queued submissions survive; they simply grade after you restart. A student
waiting an hour for a practice verdict is a non-event. A national round with
unreliable timings is not.

---

# What I need from you to go further

1. `survey-*.txt` from both servers.
2. Whether a subdomain like `judge.cmscoinformatics.org` is yours to point.
3. Whether school accounts should be self-registration, teacher-created, or
   tied to an existing school login.

With (1) I can turn Part D into exact commands for your actual machines
instead of the general form above.
