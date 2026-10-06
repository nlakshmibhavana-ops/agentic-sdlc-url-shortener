"""Run validation tools (pytest, ruff, python) inside the workspace with a timeout."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommandResult:
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def tail(self, lines: int = 25) -> str:
        return "\n".join((self.stdout + self.stderr).strip().splitlines()[-lines:])


def run_python(
    args: list[str], cwd: Path, timeout: float = 300, env: dict[str, str] | None = None
) -> CommandResult:
    full_env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    full_env.pop("PYTHONPATH", None)
    try:
        proc = subprocess.run(  # noqa: S603 - fixed interpreter, argv list, no shell
            [sys.executable, *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=full_env,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(124, exc.stdout or "", f"timed out after {timeout}s")
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


async def arun_python(
    args: list[str], cwd: Path, timeout: float = 300, env: dict[str, str] | None = None
) -> CommandResult:
    return await asyncio.to_thread(run_python, args, cwd, timeout, env)


def pytest_args(
    targets: list[str], coverage_pkg: str | None = None, cov_json: Path | None = None
) -> list[str]:
    args = ["-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-rf", *targets]
    if coverage_pkg:
        args += [f"--cov={coverage_pkg}", "--cov-report=", f"--cov-report=json:{cov_json}"]
    return args
