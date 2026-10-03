"""
Language definitions.

Two things matter here beyond the obvious compile/run commands:

  * limit scaling. A 1-second C++ limit is not a 1-second Python limit. Every
    serious judge multiplies. The defaults below are deliberate, not magic:
    Python gets 3x time and +64 MB (interpreter baseline RSS is ~12-15 MB and
    CPython's allocator is not frugal). Override per problem when a task is
    genuinely Python-hostile and you have decided that is intentional.

  * a compile step for Python. `python3 -m py_compile` turns a syntax error
    into a Compilation Error at submit time instead of a Runtime Error on
    every test, which is what a student actually needs to see.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Language:
    key: str
    name: str
    source_name: str
    compile_cmd: list[str] | None
    run_cmd: list[str]
    time_multiplier: float = 1.0
    memory_extra_mb: int = 0
    compile_processes: int = 64
    run_processes: int = 1
    editor_mode: str = "text"
    extensions: tuple[str, ...] = ()
    template: str = ""

    def compile_argv(self) -> list[str] | None:
        return list(self.compile_cmd) if self.compile_cmd else None

    def run_argv(self) -> list[str]:
        return list(self.run_cmd)


CPP_TEMPLATE = (
    "#include <iostream>\n"
    "using namespace std;\n"
    "\n\n"
    "int main() {\n"
    "    \n"
    "    \n"
    "    return 0;\n"
    "}\n"
)

PY_TEMPLATE = (
    "import sys\n"
    "\n\n"
    "def main():\n"
    "    data = sys.stdin.read().split()\n"
    "    \n"
    "\n\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)

CPP17 = Language(
    key="cpp17",
    name="C++17 (GCC)",
    source_name="solution.cpp",
    compile_cmd=[
        "/usr/bin/g++", "-x", "c++", "-std=gnu++17", "-O2", "-pipe",
        "-static", "-s", "-w",
        "-o", "solution", "solution.cpp",
    ],
    run_cmd=["./solution"],
    editor_mode="c_cpp",
    extensions=(".cpp", ".cc", ".cxx", ".c++"),
    template=CPP_TEMPLATE,
)

CPP20 = Language(
    key="cpp20",
    name="C++20 (GCC)",
    source_name="solution.cpp",
    compile_cmd=[
        "/usr/bin/g++", "-x", "c++", "-std=gnu++20", "-O2", "-pipe",
        "-static", "-s", "-w",
        "-o", "solution", "solution.cpp",
    ],
    run_cmd=["./solution"],
    editor_mode="c_cpp",
    extensions=(".cpp", ".cc", ".cxx", ".c++"),
    template=CPP_TEMPLATE,
)

PYTHON3 = Language(
    key="py3",
    name="Python 3 (CPython)",
    source_name="solution.py",
    compile_cmd=["/usr/bin/python3", "-m", "py_compile", "solution.py"],
    run_cmd=["/usr/bin/python3", "-B", "solution.py"],
    time_multiplier=3.0,
    memory_extra_mb=64,
    run_processes=8,          # CPython touches threads for some imports
    editor_mode="python",
    extensions=(".py",),
    template=PY_TEMPLATE,
)

LANGUAGES: dict[str, Language] = {
    lang.key: lang for lang in (CPP17, CPP20, PYTHON3)
}

DEFAULT_LANGUAGE = "cpp17"


def get(key: str) -> Language:
    if key not in LANGUAGES:
        raise KeyError(f"unknown language {key!r}; known: {sorted(LANGUAGES)}")
    return LANGUAGES[key]


def guess_from_filename(filename: str) -> str | None:
    """Map an uploaded file to a language key. Used by the upload path."""
    lowered = filename.lower()
    for lang in LANGUAGES.values():
        if any(lowered.endswith(ext) for ext in lang.extensions):
            return lang.key
    return None
