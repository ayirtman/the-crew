"""Experimental stage: two builders in parallel with disjoint write scopes; the Brief's api
contract is the interface. Designed to be judged by the eval and deleted if it loses.

Honest limitation: a shared tree diff cannot prove which builder wrote a file, so attribution
is by scope prefix. A builder straying into the other's scope is guarded by the prompt and by
the disjointness of the scopes, not detectable post-hoc; only files in neither scope are."""
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
from pipeline.contracts import (AudiencePack, Brief, BudgetSnapshot, BuildResult, DesignSpec, DomainPack, Plan,
                                SplitBuildResult)
from pipeline.llm import STRIP_ENV
from pipeline.stages import CallMeta
from pipeline.stages.build import ALLOWED_TOOLS
from pipeline.stages.template import diff_files, tree_hashes

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

SCOPES: dict[str, tuple[str, ...]] = {
    "backend": ("app/api/", "lib/", "tests/api/", "tests/lib/"),
    "frontend": ("app/page.tsx", "app/layout.tsx", "app/globals.css", "app/page.module.css", "tests/ui/", "components/"),
}


def in_scope(path: str, role: str) -> bool:
    return any(path == pre or path.startswith(pre) for pre in SCOPES[role])


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env.update(CI="1", NEXT_TELEMETRY_DISABLED="1", VITE_CONFIG_NATIVE_IGNORE_WARNING="true")
    return env


def _argv(prompt: str, cfg: Config) -> list[str]:
    stage = cfg.stages["build_split"]
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", stage.model or "haiku",
        "--max-turns", str(stage.max_turns or 40),
        "--allowedTools", ALLOWED_TOOLS,
        "--permission-mode", "acceptEdits",
        "--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
        "--append-system-prompt-file", str(PROMPTS / "build_system.md"),
    ]


def _task(role: str, brief: Brief, plan: Plan | None, design: DesignSpec | None,
          audience: AudiencePack | None = None, domain: DomainPack | None = None) -> str:
    drop = {"run_id", "parent", "stage", "schema_version"}
    tpl = (PROMPTS / f"build_task_split_{role}.md").read_text()
    out = tpl.replace("{{BRIEF}}", brief.model_dump_json(indent=2, exclude=drop))
    out = out.replace("{{PLAN}}", plan.model_dump_json(indent=2, exclude=drop) if plan else "none")
    if design is not None:
        out += "\n\nDESIGN:\n" + design.model_dump_json(indent=2, exclude=drop)
    if audience is not None:
        out += ("\n\nAUDIENCE RESEARCH (hard requirements on interaction; the constraints are law):\n"
                + audience.model_dump_json(indent=2, include={"patterns", "constraints"}))
    if domain is not None:
        out += ("\n\nDOMAIN RESEARCH (what the field demands; the implications are law):\n"
                + domain.model_dump_json(indent=2, include={"non_negotiables"}))
    if plan is not None:
        mine = [f.path for f in plan.files if in_scope(f.path, role)]
        if mine:
            out += ("\n\nYOU MUST WRITE every one of these files, at exactly these paths, "
                    "before you stop:\n" + "\n".join(f"- {p}" for p in mine))
    return out


def produce(*, app_dir: Path, run_dir: Path, brief: Brief, plan: Plan | None,
            design: DesignSpec | None, parent_sha: str, cfg: Config,
            audience: AudiencePack | None = None, domain: DomainPack | None = None,
            popen: Callable = subprocess.Popen,
            artifact_prefix: str = "06-build") -> tuple[SplitBuildResult, CallMeta]:
    stage = cfg.stages["build_split"]
    app_dir, run_dir = Path(app_dir), Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    before = tree_hashes(app_dir)
    t0 = time.monotonic()

    procs: dict[str, object] = {}
    for role in ("backend", "frontend"):
        prompt = _task(role, brief, plan, design, audience, domain)
        (run_dir / f"{artifact_prefix}.{role}.prompt.md").write_text(prompt)
        procs[role] = popen(_argv(prompt, cfg), cwd=app_dir, env=_env(), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True,
                            stdin=subprocess.DEVNULL)

    parts: list[BuildResult] = []
    for role in ("backend", "frontend"):
        proc = procs[role]
        remaining = max(5.0, stage.max_seconds - (time.monotonic() - t0))
        try:
            out, err = proc.communicate(timeout=remaining)
        except subprocess.TimeoutExpired:
            for p2 in procs.values():
                try:
                    os.killpg(os.getpgid(p2.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    pass
            raise BudgetExceeded(f"split build wall-clock exceeded {stage.max_seconds} seconds",
                                 BudgetSnapshot(seconds_used=time.monotonic() - t0,
                                                seconds_cap=stage.max_seconds))
        (run_dir / f"{artifact_prefix}.{role}.stderr.txt").write_text(err or "")
        try:
            raw = json.loads(out or "")
        except json.JSONDecodeError as e:
            from pipeline.stages.build import BuilderError
            raise BuilderError(f"{role} builder exited {proc.returncode} without JSON: {(err or out or '')[-500:]}") from e
        (run_dir / f"{artifact_prefix}.{role}.raw.json").write_text(json.dumps(raw, indent=2))
        parts.append(BuildResult.from_claude_json(
            raw, run_id=brief.run_id, parent=parent_sha, app_dir=str(app_dir),
            model=stage.model or "haiku", billed=stage.auth == "api_key", files_written=[],
            exit_code=proc.returncode or 0))

    wall_ms = int((time.monotonic() - t0) * 1000)
    changed = diff_files(before, tree_hashes(app_dir))
    by_role = {role: sorted(f for f in changed if in_scope(f, role)) for role in ("backend", "frontend")}
    unclaimed = sorted(f for f in changed if not any(in_scope(f, r) for r in ("backend", "frontend")))
    overlap = sorted(set(by_role["backend"]) & set(by_role["frontend"])) + unclaimed
    parts = [p.model_copy(update={"files_written": by_role[r]}) for p, r in zip(parts, ("backend", "frontend"))]

    result = SplitBuildResult(run_id=brief.run_id, parent=parent_sha, app_dir=str(app_dir),
                              parts=parts, roles=["backend", "frontend"], files_written=sorted(changed),
                              overlap=overlap)
    total_cost = sum(p.total_cost_usd_reported for p in parts)
    if stage.max_cost_usd is not None and total_cost > stage.max_cost_usd * 2:
        raise BudgetExceeded(f"split build cost {total_cost:.3f} exceeds {stage.max_cost_usd * 2}",
                             BudgetSnapshot(cost_used=total_cost, cost_cap=stage.max_cost_usd * 2))
    from pipeline.contracts import Usage
    usage = Usage(
        input_tokens=sum(p.usage.input_tokens for p in parts),
        output_tokens=sum(p.usage.output_tokens for p in parts),
        cache_creation_input_tokens=sum(p.usage.cache_creation_input_tokens for p in parts),
        cache_read_input_tokens=sum(p.usage.cache_read_input_tokens for p in parts),
    )
    meta = CallMeta(model=stage.model or "haiku", usage=usage, cost_reported=total_cost,
                    wall_ms=wall_ms, num_turns=sum(p.num_turns for p in parts))
    return result, meta
