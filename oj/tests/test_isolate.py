"""
Sandbox and verdict tests against the real isolate backend.

    cd oj && OJ_ISOLATE=/usr/local/bin/isolate python3 -m unittest tests.test_isolate -v

Skipped automatically when isolate is not installed. Run this on the server
after installing (deploy/README.md, step 6) and after every isolate or kernel
upgrade: it is the proof that verdicts are right and that a hostile submission
stays inside its box.

Every attack below is written so that it prints the *correct answer* only if
the attack failed. A sandbox hole therefore shows up as a wrong verdict, never
as a quietly passing test.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from judge.grader import Problem, judge, run_once
from judge.sandbox import open_sandbox

ROOT = Path(__file__).resolve().parent.parent
SUMSUB = Problem(ROOT / "problems" / "sumsub")
# Box ids for the tests; OJ_BOX_OFFSET is added on top, and isolate allows
# 0..999 by default, so stay well clear of both the workers and the ceiling.
BOX = int(os.environ.get("OJ_TEST_BOX", "800"))
HAVE_ISOLATE = shutil.which(os.environ.get("OJ_ISOLATE", "isolate")) is not None

# sumsub reads n then n integers and prints their sum.
SAMPLE_IN = "3\n1 2 3\n"

AC_CPP = r'''
#include <bits/stdc++.h>
using namespace std;
int main(){int n;cin>>n;long long s=0,x;for(int i=0;i<n;i++){cin>>x;s+=x;}cout<<s<<"\n";}
'''
AC_PY = '''
import sys
d = sys.stdin.read().split()
n = int(d[0])
print(sum(map(int, d[1:1 + n])))
'''

# The C++ attack harness: run attack() and only then solve the problem. If
# attack() returns true (it got out), print a wrong answer instead.
CPP_HARNESS = r'''
#include <bits/stdc++.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <netinet/in.h>
#include <arpa/inet.h>
using namespace std;
bool attack();
int main(){
  bool escaped = attack();
  int n; cin>>n; long long s=0,x; for(int i=0;i<n;i++){cin>>x;s+=x;}
  if (escaped) cout << "ESCAPED\n"; else cout << s << "\n";
}
'''


def cpp_attack(body: str) -> str:
    return CPP_HARNESS + "bool attack(){\n" + body + "\n}\n"


def py_attack(body: str) -> str:
    return ("import sys\n\ndef attack():\n"
            + "".join("    " + line + "\n" for line in body.strip().splitlines())
            + "\nescaped = attack()\n"
            + "d = sys.stdin.read().split()\n"
            + "n = int(d[0])\n"
            + "print('ESCAPED' if escaped else sum(map(int, d[1:1 + n])))\n")


@unittest.skipUnless(HAVE_ISOLATE, "isolate not installed")
class VerdictTests(unittest.TestCase):
    """The grader gives the right verdict for each kind of submission."""

    def verdict(self, source, lang="cpp17"):
        return judge(SUMSUB, source, lang, box_id=BOX).verdict.value

    def test_ac_cpp17(self):
        self.assertEqual(self.verdict(AC_CPP), "AC")

    def test_ac_cpp20(self):
        self.assertEqual(self.verdict(AC_CPP, "cpp20"), "AC")

    def test_ac_python(self):
        self.assertEqual(self.verdict(AC_PY, "py3"), "AC")

    def test_overflow_is_partial(self):
        report = judge(SUMSUB, AC_CPP.replace("long long s", "int s"), "cpp17", box_id=BOX)
        self.assertEqual((report.verdict.value, report.score), ("PA", 30.0))

    def test_wrong_answer(self):
        self.assertEqual(self.verdict('#include <cstdio>\nint main(){ puts("0"); }'), "WA")

    def test_compile_error_cpp(self):
        report = judge(SUMSUB, "int main( { return 0; }", "cpp17", box_id=BOX)
        self.assertEqual(report.verdict.value, "CE")
        self.assertIn("error", report.compile_output)

    def test_compile_error_python(self):
        self.assertEqual(self.verdict("def f(:\n  pass\n", "py3"), "CE")

    def test_segfault(self):
        self.assertEqual(self.verdict("int main(){ volatile int *p = 0; *p = 1; }"), "RE")

    def test_nonzero_exit(self):
        self.assertEqual(self.verdict("int main(){ return 3; }"), "RE")

    def test_python_exception(self):
        self.assertEqual(self.verdict("raise ValueError('x')\n", "py3"), "RE")

    def test_infinite_loop(self):
        self.assertEqual(self.verdict("int main(){ volatile long x=0; for(;;) x++; }"), "TLE")

    def test_sleep_hits_wall_clock(self):
        self.assertEqual(self.verdict("#include <unistd.h>\nint main(){ sleep(30); }"), "TLE")

    def test_python_infinite_loop(self):
        self.assertEqual(self.verdict("while True:\n    pass\n", "py3"), "TLE")

    def test_memory_bomb(self):
        src = ("#include <bits/stdc++.h>\nint main(){std::vector<char> v;"
               " for(;;) v.resize(v.size()+50*1024*1024, 1);}")
        self.assertEqual(self.verdict(src), "MLE")

    def test_python_memory_bomb(self):
        self.assertEqual(self.verdict("x = []\nwhile True:\n    x.append(bytearray(10**7))\n",
                                      "py3"), "MLE")

    def test_output_flood(self):
        src = "#include <cstdio>\nint main(){ for(;;) fputs(\"spamspamspamspam\\n\", stdout); }"
        self.assertEqual(self.verdict(src), "OLE")

    def test_feedback_first_fail_stops(self):
        from judge.grader import Feedback
        report = judge(SUMSUB, "int main(){ return 3; }", "cpp17", box_id=BOX,
                       feedback=Feedback.FIRST_FAIL)
        self.assertEqual(report.subtasks[1].verdict.value, "SK")


@unittest.skipUnless(HAVE_ISOLATE, "isolate not installed")
class EscapeTests(unittest.TestCase):
    """A hostile submission stays in its box. AC here means the attack failed."""

    @classmethod
    def setUpClass(cls):
        # A file standing in for /etc/oj/oj.env: readable by the judge
        # process, and must never be readable from, or reachable by, a box.
        fd, cls.secret = tempfile.mkstemp(prefix="oj-secret-", dir="/var/tmp")
        os.write(fd, b"TOP-SECRET-KEY\n")
        os.close(fd)
        os.chmod(cls.secret, 0o644)

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.secret)

    def assertContained(self, source, lang="cpp17"):
        report = judge(SUMSUB, source, lang, box_id=BOX)
        self.assertEqual(report.verdict.value, "AC",
                         f"attack not contained: {report.to_dict()}")

    def test_cannot_read_host_files(self):
        self.assertContained(cpp_attack(
            f'return access("{self.secret}", F_OK) == 0 '
            '|| access("/etc/passwd", F_OK) == 0 '
            '|| access("/home", F_OK) == 0;'))

    def test_cannot_write_outside_box(self):
        self.assertContained(cpp_attack(
            'int a = open("/usr/pwned", O_CREAT|O_WRONLY, 0644);'
            'int b = open("/var/tmp/pwned", O_CREAT|O_WRONLY, 0644);'
            'return a >= 0 || b >= 0;'))
        self.assertFalse(os.path.exists("/usr/pwned"))
        self.assertFalse(os.path.exists("/var/tmp/pwned"))

    def test_no_network(self):
        self.assertContained(cpp_attack(
            'int s = socket(AF_INET, SOCK_STREAM, 0); if (s < 0) return false;'
            'sockaddr_in a{}; a.sin_family = AF_INET; a.sin_port = htons(53);'
            'inet_pton(AF_INET, "1.1.1.1", &a.sin_addr);'
            'return connect(s, (sockaddr*)&a, sizeof a) == 0;'))

    def test_python_no_network(self):
        self.assertContained(py_attack('''
import socket
try:
    socket.create_connection(("1.1.1.1", 53), timeout=2)
    return True
except OSError:
    return False
'''), "py3")

    def test_fork_bomb(self):
        # Processes are capped at 1 for C++: fork must fail, and the judge
        # must come back with a verdict rather than hanging.
        self.assertContained(cpp_attack(
            'for (int i = 0; i < 1000; i++) if (fork() == 0) { for(;;) pause(); }'
            'return false;'))

    def test_python_cannot_spawn_shell(self):
        self.assertContained(py_attack('''
import subprocess
try:
    return subprocess.run(["/bin/sh", "-c", "cat /etc/passwd"],
                          capture_output=True).returncode == 0
except OSError:
    return False
'''), "py3")

    def test_cannot_kill_judge(self):
        # kill(-1) hits every process the sandbox uid may signal. The judge
        # runs as another uid, so it must survive and grade normally.
        self.assertContained(cpp_attack('kill(-1, SIGKILL); return false;'))

    def test_symlinked_input_does_not_overwrite_host_file(self):
        # Test 01 swaps input.txt for a symlink to a host file; the judge then
        # writes test 02's input. That must replace the link, not the target.
        before = Path(self.secret).read_bytes()
        self.assertContained(cpp_attack(
            f'unlink("input.txt"); symlink("{self.secret}", "input.txt"); return false;'))
        self.assertEqual(Path(self.secret).read_bytes(), before)

    def test_symlinked_output_does_not_leak_host_file(self):
        # The program replaces its own output file with a link to a secret.
        # Run Code must not return the secret, and grading must say WA (empty
        # output), not Judge Error.
        src = ('#include <unistd.h>\nint main(){ unlink("output.txt");'
               f' symlink("{self.secret}", "output.txt"); }}')
        result = run_once(SUMSUB, src, "cpp17", SAMPLE_IN, box_id=BOX)
        self.assertEqual(result.status, "ok", result.to_dict())
        self.assertNotIn("TOP-SECRET", result.stdout)
        report = judge(SUMSUB, src, "cpp17", box_id=BOX)
        self.assertEqual(report.verdict.value, "WA")

    def test_fifo_output_does_not_hang_judge(self):
        src = ('#include <unistd.h>\n#include <sys/stat.h>\nint main(){ unlink("output.txt");'
               ' mkfifo("output.txt", 0666); }')
        result = run_once(SUMSUB, src, "cpp17", SAMPLE_IN, box_id=BOX)
        self.assertEqual(result.stdout, "")


@unittest.skipUnless(HAVE_ISOLATE, "isolate not installed")
class RunOnceTests(unittest.TestCase):
    """Run Code: compile + run on the student's own input."""

    def test_echoes_output(self):
        result = run_once(SUMSUB, AC_CPP, "cpp17", SAMPLE_IN, box_id=BOX)
        self.assertEqual((result.status, result.stdout.strip()), ("ok", "6"))

    def test_python(self):
        result = run_once(SUMSUB, AC_PY, "py3", SAMPLE_IN, box_id=BOX)
        self.assertEqual((result.status, result.stdout.strip()), ("ok", "6"))

    def test_compile_error(self):
        result = run_once(SUMSUB, "int main( {", "cpp17", "", box_id=BOX)
        self.assertEqual(result.status, "compile_error")

    def test_timeout(self):
        result = run_once(SUMSUB, "int main(){ for(;;); }", "cpp17", "", box_id=BOX)
        self.assertEqual(result.status, "timeout")

    def test_stderr_returned(self):
        src = '#include <cstdio>\nint main(){ fputs("debug line", stderr); }'
        result = run_once(SUMSUB, src, "cpp17", "", box_id=BOX)
        self.assertIn("debug line", result.stderr)


@unittest.skipUnless(HAVE_ISOLATE, "isolate not installed")
class ConcurrencyTests(unittest.TestCase):
    """Two workers on different boxes grade at the same time without mixing."""

    def test_parallel_boxes(self):
        results = {}

        def work(box, n):
            data = f"{n}\n" + " ".join(["1"] * n) + "\n"
            results[box] = run_once(SUMSUB, AC_CPP, "cpp17", data, box_id=box).stdout.strip()

        threads = [threading.Thread(target=work, args=(BOX + 1 + i, 10 + i)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, {BOX + 1 + i: str(10 + i) for i in range(4)})


class BackendSelectionTests(unittest.TestCase):

    def test_no_silent_fallback_to_rlimit(self):
        saved = {k: os.environ.pop(k, None) for k in ("OJ_SANDBOX", "OJ_ISOLATE")}
        os.environ["OJ_ISOLATE"] = "/nonexistent/isolate"
        try:
            with self.assertRaises(RuntimeError):
                open_sandbox(BOX)
        finally:
            for key, value in saved.items():
                os.environ.pop(key, None)
                if value is not None:
                    os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
