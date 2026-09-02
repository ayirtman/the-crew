"""The mathematically honest Code Reviewer: one bounded fix pass, driven only by what the
oracle said (failing commands, failing test titles, uncovered criteria, missing assets).
Runs at most once per run by construction; the second verify is the verdict."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable

from pipeline.budget import BudgetExceeded
from pipeline.config import Config
from pipeline.contracts import BudgetSnapshot, BuildResult, ReviewReport, SecurityReport, TestReport
from pipeline.llm import STRIP_ENV
from pipeline.stages import CallMeta
from pipeline.stages.build import ALLOWED_TOOLS
from pipeline.stages.template import diff_files, tree_hashes

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
Runner = Callable[..., subprocess.CompletedProcess]


def failure_summary(report: TestReport, review: ReviewReport | None = None,
                    security: SecurityReport | None = None) -> str:
    """Deterministic digest of everything the oracles rejected. No model opinion enters."""
    parts: list[str] = []
    if review is not None and not review.review_pass:
        parts.append("CODE REVIEW FINDINGS:\n" + "\n".join(
            f"- [{f.kind}] {f.path}: {f.detail}" for f in review.findings))
    if security is not None and not security.security_pass:
        parts.append("SECURITY FINDINGS:\n" + "\n".join(
            f"- [{f.kind}] {f.path}: {f.detail}" for f in security.findings))
    for c in report.commands:
        if not c.passed:
            tail = (c.stderr_tail or c.stdout_tail).strip()
            parts.append(f"COMMAND FAILED: {c.name} (exit {c.exit_code})\n{tail[-1500:]}")
    failing = [t for t in report.tests if t.status == "failed"]
    if failing:
        parts.append("FAILING TESTS:\n" + "\n".join(f"- {t.title} ({t.file})" for t in failing))
    bad = [cc for cc in report.criteria_coverage if not (cc.found and cc.status == "passed")]
    if bad:
        parts.append("ACCEPTANCE CRITERIA NOT MET:\n" + "\n".join(
            f"- {cc.criterion_id}: test '{cc.test_name}' is {'missing' if not cc.found else cc.status}"
            for cc in bad))
    if report.asset_refs_missing:
        parts.append("REFERENCED ASSETS THAT DO NOT EXIST:\n" + "\n".join(f"- {a}" for a in report.asset_refs_missing))
    if report.eslint_errors:
        parts.append(f"ESLINT: {report.eslint_errors} errors (run npx eslint . to see them)")
    return "\n\n".join(parts)


def task_prompt(report: TestReport, result: BuildResult, review: ReviewReport | None = None,
                security: SecurityReport | None = None) -> str:
    tpl = (PROMPTS / "repair_task.md").read_text()
    return (tpl.replace("{{FAILURES}}", failure_summary(report, review, security))
               .replace("{{FILES}}", "\n".join(f"- {f}" for f in result.files_written)))


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update(CI="1", NEXT_TELEMETRY_DISABLED="1", VITE_CONFIG_NATIVE_IGNORE_WARNING="true")
    return env


def argv(prompt: str, cfg: Config) -> list[str]:
    stage = cfg.stages["repair"]
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", stage.model or "haiku",
        "--max-turns", str(stage.max_turns or 30),
        "--allowedTools", ALLOWED_TOOLS,
        "--permission-mode", "acceptEdits",
        "--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
        "--append-system-prompt-file", str(PROMPTS / "build_system.md"),
    ]


def produce(*, app_dir: Path, run_dir: Path, report: TestReport, build_result: BuildResult,
            parent_sha: str, cfg: Config, runner: Runner = subprocess.run,
            artifact_prefix: str = "05-repair", review: ReviewReport | None = None,
            security: SecurityReport | None = None) -> tuple[BuildResult, CallMeta]:
    stage = cfg.stages["repair"]
    app_dir, run_dir = Path(app_dir), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    before = tree_hashes(app_dir)
    prompt = task_prompt(report, build_result, review, security)
    (run_dir / f"{artifact_prefix}.prompt.md").write_text(prompt)
    t0 = time.monotonic()
    try:
        proc = runner(argv(prompt, cfg), cwd=app_dir, env=_env(), timeout=stage.max_seconds,
                      capture_output=True, text=True, start_new_session=True, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        pid = getattr(getattr(e, "process", None), "pid", None)
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise BudgetExceeded(f"repair wall-clock exceeded {stage.max_seconds} seconds",
                             BudgetSnapshot(seconds_used=time.monotonic() - t0, seconds_cap=stage.max_seconds))
    wall_ms = int((time.monotonic() - t0) * 1000)
    (run_dir / f"{artifact_prefix}.stderr.txt").write_text(proc.stderr or "")
    try:
        raw = json.loads(proc.stdout or "")
    except json.JSONDecodeError as e:
        from pipeline.stages.build import BuilderError
        raise BuilderError(
            f"claude exited {proc.returncode} without a JSON result: {(proc.stderr or proc.stdout or '')[-800:]}") from e
    (run_dir / f"{artifact_prefix}.raw.json").write_text(json.dumps(raw, indent=2))

    files = diff_files(before, tree_hashes(app_dir))
    result = BuildResult.from_claude_json(
        raw, run_id=report.run_id, parent=parent_sha, app_dir=str(app_dir), model=stage.model or "haiku",
        billed=stage.auth == "api_key", files_written=files, exit_code=proc.returncode, stage="repair",
    )
    snap = BudgetSnapshot(seconds_used=wall_ms / 1000, seconds_cap=stage.max_seconds,
                          cost_used=result.total_cost_usd_reported, cost_cap=stage.max_cost_usd)
    if stage.max_cost_usd is not None and result.total_cost_usd_reported > stage.max_cost_usd:
        raise BudgetExceeded(f"repair cost {result.total_cost_usd_reported:.3f} exceeds cap {stage.max_cost_usd}", snap)
    total_tokens = result.usage.input_tokens + result.usage.output_tokens
    if stage.max_total_tokens is not None and total_tokens > stage.max_total_tokens:
        raise BudgetExceeded(f"repair tokens {total_tokens} exceed cap {stage.max_total_tokens}", snap)
    meta = CallMeta(model=result.model, usage=result.usage, cost_reported=result.total_cost_usd_reported,
                    wall_ms=wall_ms, num_turns=result.num_turns)
    return result, meta
