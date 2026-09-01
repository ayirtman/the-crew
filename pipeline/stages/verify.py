"""Stage 6. The only stage where something that is not a model says wrong.

Order matters: vitest, eslint, next build, tsc. `next build` generates .next/types, which tsc
needs for Next's route typings, so tsc runs last. All four always run; more signal for the eval.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from pipeline.config import Config
from pipeline.contracts import (Brief, CommandResult, CriterionCoverage, Plan, TestCaseResult,
                                TestReport)

Runner = Callable[..., subprocess.CompletedProcess]
ORDER = ("vitest", "eslint", "next_build", "tsc")
ENV = {"CI": "1", "NEXT_TELEMETRY_DISABLED": "1", "VITE_CONFIG_NATIVE_IGNORE_WARNING": "true"}


def command_name(argv: list[str]) -> str:
    if "vitest" in argv:
        return "vitest"
    if "eslint" in argv:
        return "eslint"
    if "next" in argv and "build" in argv:
        return "next_build"
    if "tsc" in argv:
        return "tsc"
    return "unknown"


def _argv(name: str, run_dir: Path) -> list[str]:
    return {
        "vitest": ["npx", "vitest", "run", "--reporter=json", f"--outputFile={run_dir / 'vitest.json'}"],
        "eslint": ["npx", "eslint", ".", "--format", "json", "-o", str(run_dir / "eslint.json")],
        "next_build": ["npx", "next", "build"],
        "tsc": ["npx", "tsc", "--noEmit"],
    }[name]


def _tail(s: str | None, n: int = 40) -> str:
    return "\n".join((s or "").splitlines()[-n:])


def _run(name: str, app_dir: Path, run_dir: Path, timeout: float, runner: Runner) -> CommandResult:
    argv = _argv(name, run_dir)
    env = dict(os.environ, **ENV)
    t0 = time.monotonic()
    try:
        proc = runner(argv, cwd=app_dir, env=env, timeout=timeout, capture_output=True, text=True)
        ms = int((time.monotonic() - t0) * 1000)
        return CommandResult(name=name, argv=argv, exit_code=proc.returncode, duration_ms=ms,
                             passed=proc.returncode == 0, stdout_tail=_tail(proc.stdout),
                             stderr_tail=_tail(proc.stderr), timed_out=False)
    except subprocess.TimeoutExpired as e:
        ms = int((time.monotonic() - t0) * 1000)
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return CommandResult(name=name, argv=argv, exit_code=-1, duration_ms=ms, passed=False,
                             stdout_tail=_tail(out), stderr_tail=_tail(err), timed_out=True)


def _parse_vitest(path: Path, app_dir: Path) -> tuple[list[TestCaseResult], int, int, int]:
    if not path.exists():
        return [], 0, 0, 0
    data = json.loads(path.read_text())
    tests: list[TestCaseResult] = []
    for f in data.get("testResults", []):
        name = f.get("name", "")
        try:
            rel = Path(name).relative_to(app_dir.resolve()).as_posix()
        except ValueError:
            rel = name.split("/apps/", 1)[-1].split("/", 1)[-1] if "/apps/" in name else Path(name).name
        for a in f.get("assertionResults", []):
            status = a.get("status", "failed")
            if status not in ("passed", "failed", "skipped", "pending", "todo"):
                status = "failed"
            tests.append(TestCaseResult(file=rel, full_name=a.get("fullName", ""), title=a.get("title", ""),
                                        status=status, duration_ms=int(a.get("duration") or 0)))
    return (tests, int(data.get("numPassedTests") or 0), int(data.get("numTotalTests") or 0),
            int(data.get("numFailedTests") or 0))


def _parse_eslint(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    data = json.loads(path.read_text())
    return (sum(int(f.get("errorCount") or 0) for f in data),
            sum(int(f.get("warningCount") or 0) for f in data))


def produce(*, app_dir: Path, run_dir: Path, brief: Brief, plan: Plan | None, build_sha: str,
            cfg: Config, runner: Runner = subprocess.run) -> TestReport:
    stage = cfg.stages["verify"]
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    app_dir = Path(app_dir)
    commands: list[CommandResult] = []
    for name in ORDER:
        timeout = stage.per_command_seconds.get(name, stage.max_seconds)
        commands.append(_run(name, app_dir, run_dir, timeout, runner))

    tests, passed, total, failed = _parse_vitest(run_dir / "vitest.json", app_dir)
    errors, warnings = _parse_eslint(run_dir / "eslint.json")

    # vitest exit code is not trusted on its own: no tests, or any failed test, is a fail.
    vt = commands[0]
    vt_ok = vt.passed and total > 0 and failed == 0
    commands[0] = vt.model_copy(update={"passed": vt_ok})

    coverage: list[CriterionCoverage] = []
    if plan is not None:
        by_name: dict[str, str] = {}
        for t in tests:
            by_name.setdefault(t.title, t.status)
            by_name.setdefault(t.full_name, t.status)
        for c in plan.acceptance_criteria:
            st = by_name.get(c.test_name)
            coverage.append(CriterionCoverage(criterion_id=c.id, test_name=c.test_name,
                                              found=st is not None, status=st))

    return TestReport(
        run_id=brief.run_id, parent=build_sha, commands=commands, tests=tests,
        tests_passed=passed, tests_total=total, eslint_errors=errors, eslint_warnings=warnings,
        min_tests_required=len(brief.must_have_behaviors), criteria_coverage=coverage,
    )
