"""
Sandbox backends for executing untrusted programs.

Two backends:

  IsolateSandbox  -- production. Wraps ioi/isolate (namespaces + cgroups +
                     seccomp). This is what CMS and the IOI itself use.
  RlimitSandbox   -- development ONLY. Plain subprocess + setrlimit. It does
                     NOT contain a hostile program: no filesystem isolation,
                     no network isolation, no reliable process containment.
                     Never point this at student submissions on a real host.

Both return the same RunResult so the grader is backend-agnostic.
"""

from __future__ import annotations

import os
import shutil
import signal as _signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class RunStatus(str, Enum):
    OK = "OK"                    # exited 0 within limits
    NONZERO_EXIT = "NONZERO"     # ran, exited != 0
    TIMED_OUT = "TIMEOUT"        # CPU or wall clock exceeded
    MEMORY_EXCEEDED = "MEMORY"   # cgroup OOM / address space exceeded
    OUTPUT_EXCEEDED = "OUTPUT"   # wrote more than max_output_mb
    KILLED_BY_SIGNAL = "SIGNAL"  # segfault, abort, ...
    SANDBOX_ERROR = "SANDBOX"    # our fault, not the submission's


@dataclass
class RunLimits:
    cpu_time: float = 1.0            # seconds of CPU
    wall_time: float | None = None   # defaults to 2*cpu_time + 1.0
    memory_mb: int = 256
    max_processes: int = 1           # raise for compilers / threaded runtimes
    max_output_mb: int = 64          # guards against a while(1) printf bomb
    stack_mb: int | None = None      # None -> same as memory limit

    def effective_wall(self) -> float:
        return self.wall_time if self.wall_time is not None else 2.0 * self.cpu_time + 1.0


@dataclass
class RunResult:
    status: RunStatus
    exit_code: int = 0
    signal: int | None = None
    cpu_time: float = 0.0
    wall_time: float = 0.0
    memory_kb: int = 0
    stdout_path: Path | None = None
    stderr_text: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is RunStatus.OK


@dataclass
class Sandbox:
    """Base interface. A sandbox owns a scratch directory ('the box')."""

    box_dir: Path = field(init=False)

    # The box is writable by the submission, so between runs it may hold
    # whatever the program left there. isolate already deletes symlinks and
    # other special files after every run (unless --special-files), which is
    # what stops a program from swapping output.txt for a link to, say,
    # /etc/oj/oj.env. We do not rely on that alone: every host-side write
    # unlinks first and creates with O_EXCL|O_NOFOLLOW, and every host-side
    # read goes through _capture(), which only ever reads a plain file. A
    # missing or odd output file then reads as empty output -- Wrong Answer --
    # instead of crashing the checker into a Judge Error.

    def put(self, src: Path | str, name: str | None = None) -> Path:
        src = Path(src)
        return self.write(name or src.name, src.read_bytes())

    def write(self, name: str, content: str | bytes) -> Path:
        dst = self.box_dir / name
        data = content if isinstance(content, bytes) else content.encode("utf-8")
        try:
            os.unlink(dst)
        except FileNotFoundError:
            pass
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        return dst

    def _capture(self, name: str, limit_bytes: int) -> tuple[Path, int]:
        """Copy a box file the program wrote into a host-private directory.

        Returns (private path, size of the original). Anything that is not a
        regular file -- symlink, FIFO, device, directory -- is treated as empty.
        """
        if not hasattr(self, "_private"):
            self._private = Path(tempfile.mkdtemp(prefix="oj-capture-"))
        dst = self._private / name
        size = 0
        with open(dst, "wb") as out:
            try:
                fd = os.open(self.box_dir / name,
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError:
                return dst, 0
            with os.fdopen(fd, "rb") as fh:
                info = os.fstat(fh.fileno())
                if stat.S_ISREG(info.st_mode):
                    size = info.st_size
                    out.write(fh.read(limit_bytes))
        return dst, size

    def _close_private(self) -> None:
        private = getattr(self, "_private", None)
        if private is not None:
            shutil.rmtree(private, ignore_errors=True)

    def get(self, name: str) -> Path:
        return self.box_dir / name

    def run(self, argv: list[str], limits: RunLimits, *,
            stdin: str | None = None, stdout: str = "stdout.txt",
            env: dict[str, str] | None = None) -> RunResult:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# --------------------------------------------------------------------------
# Production backend
# --------------------------------------------------------------------------

class IsolateSandbox(Sandbox):
    """
    Wrapper around `isolate`. Requires isolate installed and cgroups v2
    configured; run `isolate-check-environment` once at deploy time.

    box_id must be unique per concurrent judge process (0..999). Pin one judge
    process per CPU core and give each a fixed box_id.
    """

    def __init__(self, box_id: int = 0, binary: str | None = None,
                 use_cgroups: bool | None = None):
        self.box_id = box_id
        self.binary = binary or os.environ.get("OJ_ISOLATE", "isolate")
        self.major = self._detect_version(self.binary)
        # Every isolate release, 1.x and 2.x alike, needs --cg to use control
        # groups; without it --cg-mem is rejected and the only memory limit
        # left is an address-space rlimit, which turns a memory bomb into a
        # std::bad_alloc abort (RE) rather than MLE and miscounts Python.
        # OJ_ISOLATE_CG=0 exists only for a host with no cgroup support.
        if use_cgroups is None:
            use_cgroups = os.environ.get("OJ_ISOLATE_CG", "1") != "0"
        self.cg = ["--cg"] if use_cgroups else []
        subprocess.run([self.binary, *self.cg, f"--box-id={box_id}", "--cleanup"],
                       capture_output=True)
        out = subprocess.run(
            [self.binary, *self.cg, f"--box-id={box_id}", "--init"],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            raise RuntimeError(f"isolate --init failed for box {box_id}: "
                               f"{(out.stderr or out.stdout).strip()[:500]}")
        self.box_dir = Path(out.stdout.strip()) / "box"

    @staticmethod
    def _detect_version(binary: str) -> int:
        try:
            proc = subprocess.run([binary, "--version"], capture_output=True,
                                  text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return 1
        text = (proc.stdout or "") + (proc.stderr or "")
        for token in text.replace("v", " ").split():
            head = token.split(".")[0]
            if head.isdigit():
                return int(head)
        return 1

    def run(self, argv, limits, *, stdin=None, stdout="stdout.txt", env=None):
        meta_fd, meta_path = tempfile.mkstemp(prefix="isolate-meta-")
        os.close(meta_fd)

        mem_kb = limits.memory_mb * 1024
        cmd = [
            self.binary, *self.cg, f"--box-id={self.box_id}",
            f"--meta={meta_path}",
            f"--time={limits.cpu_time}",
            f"--wall-time={limits.effective_wall()}",
            "--extra-time=0.5",
            f"--processes={limits.max_processes}",
            f"--fsize={limits.max_output_mb * 1024}",
            "--stderr=stderr.txt",
            f"--stdout={stdout}",
        ]
        cmd.append(f"--cg-mem={mem_kb}" if self.cg else f"--mem={mem_kb}")
        if limits.stack_mb is not None:
            cmd.append(f"--stack={limits.stack_mb * 1024}")
        if stdin is not None:
            cmd.append(f"--stdin={stdin}")

        # Debian/Ubuntu install g++ and python3 as symlinks into
        # /etc/alternatives, and isolate does not bind /etc by default, so
        # without this every compile fails with "execve: No such file".
        # Only the alternatives directory is exposed, read-only.
        cmd.append("--dir=/etc/alternatives:maybe")

        environment = {"PATH": "/usr/bin:/bin", "HOME": "/box",
                       "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8"}
        environment.update(env or {})
        for key, value in environment.items():
            cmd.append(f"--env={key}={value}")

        cmd += ["--run", "--", *argv]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        meta = self._parse_meta(meta_path)
        os.unlink(meta_path)
        if not meta.get("status") and proc.returncode not in (0, 1):
            # isolate itself failed (bad flag, box not initialised, ...).
            meta = {"status": "XX",
                    "message": (proc.stderr or "isolate failed").strip()[:500]}

        output_cap = limits.max_output_mb * 1024 * 1024
        stdout_path, stdout_size = self._capture(stdout, output_cap)
        err_path, _ = self._capture("stderr.txt", 8000)
        stderr_text = err_path.read_text(errors="replace")

        result = RunResult(
            status=RunStatus.OK,
            exit_code=int(meta.get("exitcode", 0)),
            signal=int(meta["exitsig"]) if "exitsig" in meta else None,
            cpu_time=float(meta.get("time", 0.0)),
            wall_time=float(meta.get("time-wall", 0.0)),
            memory_kb=int(meta.get("cg-mem", meta.get("max-rss", 0))),
            stdout_path=stdout_path,
            stderr_text=stderr_text,
            message=meta.get("message", ""),
        )

        status = meta.get("status", "")
        if status == "XX":
            result.status = RunStatus.SANDBOX_ERROR
        elif meta.get("cg-oom-killed") == "1":
            result.status = RunStatus.MEMORY_EXCEEDED
        elif result.signal == _signal.SIGXFSZ or stdout_size >= output_cap:
            result.status = RunStatus.OUTPUT_EXCEEDED
        elif status == "TO":
            result.status = RunStatus.TIMED_OUT
        elif status == "SG":
            # SIGSEGV on a memory-capped run is usually really MLE.
            oom_signals = {9, 11}
            near_cap = result.memory_kb >= mem_kb * 0.95
            if result.signal in oom_signals and near_cap:
                result.status = RunStatus.MEMORY_EXCEEDED
            else:
                result.status = RunStatus.KILLED_BY_SIGNAL
        elif status == "RE":
            result.status = RunStatus.NONZERO_EXIT
        elif result.exit_code != 0:
            result.status = RunStatus.NONZERO_EXIT

        return result

    @staticmethod
    def _parse_meta(path: str) -> dict[str, str]:
        meta: dict[str, str] = {}
        with open(path) as fh:
            for line in fh:
                if ":" in line:
                    key, _, value = line.partition(":")
                    meta[key.strip()] = value.strip()
        return meta

    def close(self):
        subprocess.run([self.binary, *self.cg, f"--box-id={self.box_id}", "--cleanup"],
                       capture_output=True)
        self._close_private()


# --------------------------------------------------------------------------
# Development backend -- NOT SECURE
# --------------------------------------------------------------------------

class RlimitSandbox(Sandbox):
    """
    Local-development stand-in so the grader can be exercised on a laptop or
    in WSL without isolate. Enforces resource limits, isolates nothing.

    Caveat: memory_kb comes from RUSAGE_CHILDREN, which is a high-water mark
    across every child this process has ever spawned -- including the
    compiler. Treat memory figures from this backend as meaningless. Only
    isolate's cgroup accounting gives a real per-run number, which is one more
    reason MLE verdicts must never be trusted from a dev run.
    """

    def __init__(self, box_id: int = 0):
        self.box_id = box_id
        self._tmp = tempfile.mkdtemp(prefix=f"devbox-{box_id}-")
        self.box_dir = Path(self._tmp)

    def run(self, argv, limits, *, stdin=None, stdout="stdout.txt", env=None):
        import resource

        mem_bytes = limits.memory_mb * 1024 * 1024
        cpu_cap = int(limits.cpu_time) + 1

        def apply_limits():
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_cap, cpu_cap + 1))
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
            resource.setrlimit(resource.RLIMIT_FSIZE,
                               (limits.max_output_mb * 1024 * 1024,) * 2)
            resource.setrlimit(resource.RLIMIT_NPROC,
                               (limits.max_processes + 16,) * 2)
            os.setsid()

        environment = {"PATH": "/usr/bin:/bin", "HOME": str(self.box_dir),
                       "PYTHONIOENCODING": "utf-8", "LANG": "C.UTF-8"}
        environment.update(env or {})

        out_path = self.box_dir / stdout
        stdin_fh = open(self.box_dir / stdin, "rb") if stdin else subprocess.DEVNULL
        started = time.monotonic()
        usage_before = resource.getrusage(resource.RUSAGE_CHILDREN)

        try:
            with open(out_path, "wb") as out_fh:
                proc = subprocess.Popen(
                    argv, cwd=self.box_dir, stdin=stdin_fh, stdout=out_fh,
                    stderr=subprocess.PIPE, env=environment, preexec_fn=apply_limits,
                )
                try:
                    _, err = proc.communicate(timeout=limits.effective_wall())
                    timed_out = False
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _, err = proc.communicate()
                    timed_out = True
        finally:
            if stdin_fh is not subprocess.DEVNULL:
                stdin_fh.close()

        usage_after = resource.getrusage(resource.RUSAGE_CHILDREN)
        wall = time.monotonic() - started
        cpu = ((usage_after.ru_utime - usage_before.ru_utime)
               + (usage_after.ru_stime - usage_before.ru_stime))
        peak_kb = max(usage_after.ru_maxrss, 0)

        output_cap = limits.max_output_mb * 1024 * 1024
        out_path, out_size = self._capture(stdout, output_cap)

        result = RunResult(
            status=RunStatus.OK,
            exit_code=proc.returncode if proc.returncode and proc.returncode > 0 else 0,
            signal=-proc.returncode if proc.returncode and proc.returncode < 0 else None,
            cpu_time=round(cpu, 3),
            wall_time=round(wall, 3),
            memory_kb=peak_kb,
            stdout_path=out_path,
            stderr_text=(err or b"").decode(errors="replace")[:8000],
        )

        if timed_out or cpu > limits.cpu_time + 0.5:
            result.status = RunStatus.TIMED_OUT
        elif result.signal == _signal.SIGXFSZ or out_size >= output_cap:
            result.status = RunStatus.OUTPUT_EXCEEDED
        elif result.signal in (9, 11) and peak_kb >= limits.memory_mb * 1024 * 0.95:
            result.status = RunStatus.MEMORY_EXCEEDED
        elif result.signal is not None:
            result.status = RunStatus.KILLED_BY_SIGNAL
        elif result.exit_code != 0:
            result.status = RunStatus.NONZERO_EXIT

        return result

    def close(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        self._close_private()


def open_sandbox(box_id: int = 0, backend: str | None = None) -> Sandbox:
    """
    Pick a backend: explicit arg, else $OJ_SANDBOX, else isolate.

    There is deliberately no automatic fallback to the rlimit backend: a
    server where isolate went missing must fail loudly with Judge Errors, not
    quietly start running student code with no isolation at all. Local
    development opts in with OJ_SANDBOX=rlimit.

    $OJ_BOX_OFFSET is added to box_id. On a host shared with CMS this MUST be
    set (e.g. 100) so our boxes never collide with CMS's, which start at 0.
    Two processes initialising the same box id will clobber each other's
    working directory mid-run and produce nonsense verdicts on both sides.
    """
    backend = backend or os.environ.get("OJ_SANDBOX") or "isolate"
    box_id += int(os.environ.get("OJ_BOX_OFFSET", "0"))
    if backend == "isolate":
        binary = os.environ.get("OJ_ISOLATE", "isolate")
        if not shutil.which(binary):
            raise RuntimeError(
                f"isolate not found ({binary!r}). Install it or set OJ_ISOLATE; "
                "for local development only, OJ_SANDBOX=rlimit.")
        return IsolateSandbox(box_id)
    if backend == "rlimit":
        return RlimitSandbox(box_id)
    raise ValueError(f"unknown sandbox backend {backend!r}")
