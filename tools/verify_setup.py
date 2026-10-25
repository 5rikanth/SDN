#!/usr/bin/env python3
"""Verify an offline development setup using only the Python standard library."""

from __future__ import annotations

import importlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
IMPORT_NAMES: Dict[str, str] = {
    "dnspython": "dns",
    "pyyaml": "yaml",
    "pytest-cov": "pytest_cov",
}
CONTAINER_ONLY = {"ryu", "mininet"}


def result(level: str, check: str, detail: str, hint: Optional[str] = None) -> None:
    """Print one consistent, actionable verification result."""
    print("{} - {}: {}".format(level, check, detail))
    if hint:
        print("       Fix: " + hint)


def requirement_names(path: Path) -> List[str]:
    """Extract distribution names from the project's pinned requirements file."""
    names = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = re.match(r"[A-Za-z0-9_.-]+", line)
        if match:
            names.append(match.group(0))
    return names


def verify_python(failures: List[str]) -> None:
    """Check the supported offline floor and explain the special Ryu pin."""
    version = sys.version_info
    rendered = "{}.{}.{}".format(version.major, version.minor, version.micro)
    if version[:2] != (3, 9):
        failures.append("Python version")
        result(
            "FAIL",
            "Python version",
            rendered + " (the project pins Python 3.9)",
            "use Python 3.9 for the host workflow; live Ryu remains container-only",
        )
    else:
        result("PASS", "Python version", rendered + " (project-pinned version)")


def verify_imports(failures: List[str]) -> None:
    """Import each host dependency and identify live-only packages explicitly."""
    requirements = requirement_names(ROOT / "requirements-dev.txt")
    for distribution in requirements:
        key = distribution.lower()
        if key == "ryu":
            result(
                "INFO",
                "package ryu",
                "container only, not required here",
            )
            continue
        module_name = IMPORT_NAMES.get(key, key.replace("-", "_"))
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - diagnostics must continue
            failures.append("package " + distribution)
            result(
                "FAIL",
                "package " + distribution,
                "cannot import {} ({})".format(module_name, exc),
                "run: python -m pip install -r requirements-dev.txt",
            )
        else:
            result("PASS", "package " + distribution, "imported " + module_name)
    result("INFO", "package mininet", "container only, not required here")


def verify_directories(failures: List[str]) -> None:
    """Ensure the minimum repository structure survived download and extraction."""
    for name in ("src", "topology", "tests", "intents"):
        if (ROOT / name).is_dir():
            result("PASS", "directory " + name, "present")
        else:
            failures.append("directory " + name)
            result(
                "FAIL",
                "directory " + name,
                "missing",
                "clone or reset the GitHub repository, then retry",
            )


def run_command(arguments: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
    """Run a repository command with captured text for concise diagnostics."""
    return subprocess.run(
        arguments,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def output_tail(completed: subprocess.CompletedProcess, lines: int = 12) -> str:
    """Return the useful final lines from a failed subprocess."""
    combined = (completed.stdout or "") + (completed.stderr or "")
    return "\n".join(combined.strip().splitlines()[-lines:])


def verify_tests(failures: List[str]) -> None:
    """Run the complete software-only suite and report its real pass count."""
    try:
        completed = run_command([sys.executable, "-m", "pytest", "tests", "-q"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        failures.append("pytest suite")
        result("FAIL", "pytest suite", str(exc), "check Python and pytest installation")
        return
    summary = output_tail(completed, lines=20)
    matches = re.findall(r"(\d+) passed", summary)
    if completed.returncode == 0 and matches:
        result("PASS", "pytest suite", matches[-1] + " tests passed")
    else:
        failures.append("pytest suite")
        result("FAIL", "pytest suite", "tests did not pass")
        print(summary)
        result("INFO", "pytest help", "read the first FAILED entry above")


def verify_demo(failures: List[str]) -> None:
    """Run the ST-4 proof-of-work demo without interpreting its timing."""
    try:
        completed = run_command([sys.executable, "demo_st4.py"])
    except (OSError, subprocess.TimeoutExpired) as exc:
        failures.append("demo_st4.py")
        result("FAIL", "demo_st4.py", str(exc), "verify the release was fully extracted")
        return
    if completed.returncode == 0:
        result("PASS", "demo_st4.py", "completed without raising")
    else:
        failures.append("demo_st4.py")
        result("FAIL", "demo_st4.py", "returned code " + str(completed.returncode))
        print(output_tail(completed))


def verify_docker() -> None:
    """Report optional Docker availability without blocking offline work."""
    executable = shutil.which("docker")
    if executable:
        result("PASS", "Docker", "found on PATH")
    else:
        result(
            "INFO",
            "Docker",
            "not on PATH; not required for software-only ST-7 work",
            "install Docker only when you need the live Mininet network",
        )


def main() -> int:
    """Run every setup check and return failure only for required capabilities."""
    failures: List[str] = []
    verify_python(failures)
    verify_directories(failures)
    if sys.version_info[:2] == (3, 9):
        verify_imports(failures)
        verify_tests(failures)
        verify_demo(failures)
    else:
        result("SKIP", "runtime checks", "install Python 3.9 to run the host workflow")
    verify_docker()
    if failures:
        print("Setup incomplete - see failures above")
        return 1
    print("Setup OK - software-only checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
