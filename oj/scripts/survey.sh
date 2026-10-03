#!/usr/bin/env bash
# Read-only survey of a server that already runs CMS and/or Michanicos.
#
# This script CHANGES NOTHING. It only reads. Run it on each of the two
# servers and keep the output -- it answers the questions that decide whether
# the school judge can share the box, and with what settings.
#
#   bash survey.sh > survey-$(hostname).txt 2>&1
#
# Some sections need root to be useful; it will run without and just show
# less. Nothing here touches CMS's database or configuration.

set -u

section() { printf '\n=== %s ===\n' "$1"; }

section "Host"
hostname
cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)='
uname -r
echo "uptime: $(uptime -p 2>/dev/null)"

section "CPU"
nproc
lscpu 2>/dev/null | grep -E 'Model name|^CPU\(s\)|Thread|Core|MHz|Hypervisor|Virtual'
echo "--- governor ---"
cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | sort | uniq -c
echo "--- kernel cmdline (looking for isolcpus/nohz_full) ---"
cat /proc/cmdline
echo "--- current load ---"
cat /proc/loadavg

section "Memory and disk"
free -h
df -h / /var /home 2>/dev/null

section "Virtualisation type (KVM vs container)"
systemd-detect-virt 2>/dev/null || echo "systemd-detect-virt not available"

section "cgroups version"
stat -fc %T /sys/fs/cgroup/ 2>/dev/null
echo "(cgroup2fs = v2, tmpfs = v1)"

section "isolate"
for candidate in isolate /usr/local/bin/isolate /usr/bin/isolate; do
  path=$(command -v "$candidate" 2>/dev/null || true)
  [ -n "$path" ] && { echo "found: $path"; "$path" --version 2>&1 | head -2; break; }
done
echo "--- isolate config ---"
cat /usr/local/etc/isolate 2>/dev/null || cat /etc/isolate 2>/dev/null || echo "(no isolate config file found)"
echo "--- existing sandbox dirs (tells us which box ids are in use) ---"
ls -1 /var/local/lib/isolate 2>/dev/null | head -40 || echo "(none / not readable)"

section "CMS"
command -v cmsResourceService >/dev/null 2>&1 && echo "CMS binaries on PATH" || echo "CMS binaries not on PATH"
python3 -c "import cms, sys; print('cms package', getattr(cms,'__version__','?'))" 2>/dev/null || echo "(cms python package not importable as this user)"
echo "--- cms.conf worker/sandbox settings ---"
grep -E '"(max_file_size|keep_sandbox|num_.*|core_.*|ServiceCoord|Worker)"' \
     /usr/local/etc/cms.conf 2>/dev/null | head -30 || echo "(cms.conf not readable here)"

section "systemd units of interest"
systemctl list-units --type=service --all 2>/dev/null \
  | grep -Ei 'cms|michanicos|judge|nginx|postgres|redis|gunicorn|uwsgi' || echo "(none matched)"

section "Listening ports"
(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | head -40

section "PostgreSQL"
psql --version 2>/dev/null || echo "(psql not installed)"
sudo -n -u postgres psql -tAc "\l" 2>/dev/null | cut -d'|' -f1 || echo "(cannot list databases without root)"

section "nginx"
nginx -v 2>&1 || echo "(nginx not installed)"
ls -1 /etc/nginx/sites-enabled/ 2>/dev/null
echo "--- server_name directives ---"
grep -rh 'server_name' /etc/nginx/sites-enabled/ /etc/nginx/conf.d/ 2>/dev/null | sed 's/^[[:space:]]*//' | sort -u

section "TLS certificates"
certbot certificates 2>/dev/null | grep -E 'Certificate Name|Domains|Expiry' || echo "(certbot not available / needs root)"

section "Toolchain versions"
g++ --version 2>/dev/null | head -1
python3 --version
echo "done."
