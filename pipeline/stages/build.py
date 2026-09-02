"""Stage 5. Brief (+ Plan) in, an app in a cloned template out.

The builder is Claude Code headless on the subscription. Tools are confined to the app dir and
to three npm commands; nothing bypasses permissions. The pipeline never reads the code the
builder writes, it only measures it.
"""
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
from pipeline.contracts import Brief, BudgetSnapshot, BuildResult, DesignSpec, Plan
from pipeline.llm import STRIP_ENV
from pipeline.stages import CallMeta
from pipeline.stages.template import diff_files, tree_hashes

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
Runner = Callable[..., subprocess.CompletedProcess]

ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep,Bash(npx vitest run*),Bash(npx tsc*),Bash(npx eslint*)"


class BuilderError(Exception):
    pass


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update(CI="1", NEXT_TELEMETRY_DISABLED="1", VITE_CONFIG_NATIVE_IGNORE_WARNING="true")
    return env


def task_prompt(brief: Brief, plan: Plan | None, design: DesignSpec | None = None) -> str:
    drop = {"run_id", "parent", "stage", "schema_version"}
    brief_json = brief.model_dump_json(indent=2, exclude=drop)
    if plan is None:
        prompt = (PROMPTS / "build_task_v0.md").read_text().replace("{{BRIEF}}", brief_json)
    else:
        plan_json = plan.model_dump_json(indent=2, exclude=drop)
        prompt = ((PROMPTS / "build_task_v1.md").read_text()
                  .replace("{{BRIEF}}", brief_json).replace("{{PLAN}}", plan_json))
    if design is not None:
        prompt += ("\n\nDESIGN:\n" + design.model_dump_json(indent=2, exclude=drop)
                   + "\nUse only the corpus component classes named here; they exist in app/globals.css "
                     "and are described in design/corpus.md. Do not invent new component styles.")
    return prompt


def argv(prompt: str, cfg: Config) -> list[str]:
    stage = cfg.stages["build"]
    a = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", stage.model or "haiku",
        "--max-turns", str(stage.max_turns or 12),
        "--allowedTools", ALLOWED_TOOLS,
        "--permission-mode", "acceptEdits",
        "--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
        "--append-system-prompt-file", str(PROMPTS / "build_system.md"),
    ]
    if stage.auth == "api_key" and stage.max_cost_usd is not None:
        a += ["--max-budget-usd", str(stage.max_cost_usd)]
    return a


def produce(*, app_dir: Path, run_dir: Path, brief: Brief, plan: Plan | None, parent_sha: str,
            cfg: Config, runner: Runner = subprocess.run, design: DesignSpec | None = None,
            artifact_prefix: str = "03-build") -> tuple[BuildResult, CallMeta]:
    stage = cfg.stages["build"]
    app_dir, run_dir = Path(app_dir), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    before = tree_hashes(app_dir)
    prompt = task_prompt(brief, plan, design=design)
    (run_dir / f"{artifact_prefix}.prompt.md").write_text(prompt)
    t0 = time.monotonic()
    try:
        proc = runner(argv(prompt, cfg), cwd=app_dir, env=_env(), timeout=stage.max_seconds,
                      capture_output=True, text=True, start_new_session=True, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as e:
        # Claude Code spawns node children; kill the whole session so nothing leaks.
        pid = getattr(getattr(e, "process", None), "pid", None)
        if pid:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        raise BudgetExceeded(f"build wall-clock exceeded {stage.max_seconds} seconds",
                             BudgetSnapshot(seconds_used=time.monotonic() - t0, seconds_cap=stage.max_seconds))
    wall_ms = int((time.monotonic() - t0) * 1000)
    (run_dir / f"{artifact_prefix}.stderr.txt").write_text(proc.stderr or "")
    # claude exits non-zero on max_turns and still prints the full result; keep it as data.
    try:
        raw = json.loads(proc.stdout or "")
    except json.JSONDecodeError as e:
        raise BuilderError(
            f"claude exited {proc.returncode} without a JSON result: {(proc.stderr or proc.stdout or '')[-800:]}") from e
    (run_dir / f"{artifact_prefix}.raw.json").write_text(json.dumps(raw, indent=2))

    files = diff_files(before, tree_hashes(app_dir))
    result = BuildResult.from_claude_json(
        raw, run_id=brief.run_id, parent=parent_sha, app_dir=str(app_dir), model=stage.model or "haiku",
        billed=stage.auth == "api_key", files_written=files, exit_code=proc.returncode,
    )
    total_tokens = result.usage.input_tokens + result.usage.output_tokens
    snap = BudgetSnapshot(tokens_used=total_tokens, tokens_cap=stage.max_total_tokens,
                          seconds_used=wall_ms / 1000, seconds_cap=stage.max_seconds,
                          cost_used=result.total_cost_usd_reported, cost_cap=stage.max_cost_usd)
    if stage.max_cost_usd is not None and result.total_cost_usd_reported > stage.max_cost_usd:
        raise BudgetExceeded(f"build cost {result.total_cost_usd_reported:.3f} exceeds cap {stage.max_cost_usd}", snap)
    if stage.max_total_tokens is not None and total_tokens > stage.max_total_tokens:
        raise BudgetExceeded(f"build tokens {total_tokens} exceed cap {stage.max_total_tokens}", snap)
    meta = CallMeta(model=result.model, usage=result.usage, cost_reported=result.total_cost_usd_reported,
                    wall_ms=wall_ms, num_turns=result.num_turns)
    return result, meta
