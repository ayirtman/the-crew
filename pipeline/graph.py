"""The thin supervisor. Routes, holds the budget, writes artifacts. Never reasons about the product.

Two variants share every node: v0 = intake -> build -> verify, v1 = intake -> plan -> build -> verify.
State carries paths and hashes, not objects; every node reloads its input from disk and verifies it,
which is what makes a cascade traceable when something goes wrong.
"""
from __future__ import annotations

import datetime as dt
import functools
import logging
import operator
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Callable, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from pipeline import artifacts, evaluators
from pipeline.artifacts import ArtifactError
from pipeline.budget import BudgetExceeded, Ledger, canonical_model, cost_for
from pipeline.config import Config
from pipeline.contracts import (Brief, BuildResult, Plan, RunManifest, StageFailure, StageRecord,
                                TestReport, Usage)
from pipeline.llm import CallerError, SchemaInvalid, StructuredCaller
from pipeline.stages import CallMeta, intake, plan as plan_stage, template
from pipeline.stages.build import BuilderError

log = logging.getLogger("pipeline")

Variant = Literal["v0", "v1"]
SEQ = {"intake": 1, "plan": 2, "build": 3, "verify": 4}
# stage name -> artifact name (file stem) and state key prefix
ARTIFACT = {"intake": "brief", "plan": "plan", "build": "build", "verify": "verify"}
STATE_KEY = {"intake": "brief", "plan": "plan", "build": "build", "verify": "report"}


@dataclass
class Deps:
    cfg: Config
    caller: StructuredCaller
    build: Callable[..., tuple[BuildResult, CallMeta]]
    verify: Callable[..., TestReport]
    template_dir: Path
    apps_dir: Path
    runs_dir: Path


class PipelineState(TypedDict, total=False):
    run_id: str
    graph: Variant
    idea_id: str
    idea_path: str
    idea_sha: str
    brief_path: str
    brief_sha: str
    plan_path: str
    plan_sha: str
    build_path: str
    build_sha: str
    report_path: str
    report_sha: str
    failure: dict | None
    status: str
    stage_records: Annotated[list[dict], operator.add]


@dataclass
class Outcome:
    status: str
    run_id: str
    run_dir: Path
    manifest: RunManifest


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@functools.lru_cache(maxsize=1)
def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5, cwd=Path(__file__).resolve().parents[1]).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


@functools.lru_cache(maxsize=1)
def _claude_version() -> str | None:
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=10,
                              stdin=subprocess.DEVNULL).stdout.strip() or None
    except Exception:
        return None


class _Run:
    """Per-run mutable context: ledger, manifest, run dir. One instance per compiled graph."""

    def __init__(self, deps: Deps, variant: Variant, run_id: str, idea_id: str):
        self.deps, self.variant, self.run_id, self.idea_id = deps, variant, run_id, idea_id
        self.run_dir = Path(deps.runs_dir) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(max_cost_usd=deps.cfg.run.max_cost_usd, max_seconds=deps.cfg.run.max_seconds)
        self.records: list[StageRecord] = []
        self.started_at = _now()
        tv = Path(deps.template_dir).parent / "TEMPLATE_VERSION"
        self.template_version = tv.read_text().strip() if tv.exists() else "0"
        self.write_manifest("running", None)

    def write_manifest(self, status: str, failed_stage: str | None, finished: bool = False) -> RunManifest:
        m = RunManifest(
            run_id=self.run_id, graph=self.variant, idea_id=self.idea_id, started_at=self.started_at,
            finished_at=_now() if finished else None, status=status, failed_stage=failed_stage,
            stages=list(self.records), config_snapshot=self.deps.cfg.snapshot(),
            pipeline_git_sha=_git_sha(), template_version=self.template_version,
            claude_code_version=_claude_version(),
        )
        (self.run_dir / "00-manifest.json").write_text(m.model_dump_json(indent=2))
        return m

    # ---- the stage wrapper -------------------------------------------------------------

    def stage(self, name: str, produce: Callable[[PipelineState], tuple[Any, CallMeta, list[str]]],
              evaluate: Callable[[Any, PipelineState], list[str]]) -> Callable[[PipelineState], dict]:
        seq = SEQ[name]

        def node(state: PipelineState) -> dict:
            t0 = time.monotonic()
            log.info("[%s] start", name)
            rec = dict(stage=name, artifact_path="", artifact_sha256="", model="", input_tokens=0,
                       output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0,
                       billed_usd=0.0, wall_ms=0, evaluator_passed=False, evaluator_reasons=[],
                       upstream_rejections=0)

            def fail(kind: str, reasons: list[str], rejected: str | None = None, budget=None) -> dict:
                f = StageFailure(stage=name, run_id=self.run_id, kind=kind, reasons=reasons,
                                 rejected_artifact_path=rejected, budget=budget)
                artifacts.write(self.run_dir, seq, "failure", f)
                rec["wall_ms"] = int((time.monotonic() - t0) * 1000)
                rec["evaluator_reasons"] = reasons
                self.records.append(StageRecord(**rec))
                self.write_manifest("running", None)
                log.info("[%s] FAILED %s: %s", name, kind, "; ".join(reasons)[:300])
                return {"failure": f.model_dump(), "stage_records": [rec]}

            try:
                self.ledger.check()
            except BudgetExceeded as e:
                return fail("budget_exceeded", [e.reason], budget=e.snapshot)

            try:
                artifact, meta, upstream = produce(state)
            except ArtifactError as e:
                rec["upstream_rejections"] = 1
                return fail("schema_invalid", [f"upstream artifact rejected: {e}"])
            except SchemaInvalid as e:
                return fail("schema_invalid", e.reasons)
            except BudgetExceeded as e:
                return fail("budget_exceeded", [e.reason], budget=e.snapshot)
            except (CallerError, BuilderError) as e:
                return fail("subprocess_error", [str(e)])

            path, sha = artifacts.write(self.run_dir, seq, ARTIFACT[name], artifact)
            cost = meta.cost_reported if meta.cost_reported else cost_for(meta.usage, meta.model, self.deps.cfg)
            # subscription stages report a notional cost and bill nothing; api_key stages bill what they report
            billed = cost if self.deps.cfg.stages[name].auth == "api_key" else 0.0
            wall = meta.wall_ms or int((time.monotonic() - t0) * 1000)
            rec.update(artifact_path=str(path), artifact_sha256=sha, model=canonical_model(meta.model),
                       input_tokens=meta.usage.input_tokens, output_tokens=meta.usage.output_tokens,
                       cache_read_tokens=meta.usage.cache_read_input_tokens,
                       cache_write_tokens=meta.usage.cache_creation_input_tokens,
                       cost_usd=cost, billed_usd=billed, wall_ms=wall, upstream_rejections=len(upstream))
            self.ledger.add(cost_usd=cost, wall_ms=wall,
                            tokens=meta.usage.input_tokens + meta.usage.output_tokens)

            reasons = evaluate(artifact, state)
            if reasons:
                rec["evaluator_reasons"] = reasons
                self.records.append(StageRecord(**rec))
                self.write_manifest("running", None)
                f = StageFailure(stage=name, run_id=self.run_id, kind="evaluator_rejected", reasons=reasons,
                                 rejected_artifact_path=str(path))
                artifacts.write(self.run_dir, seq, "failure", f)
                log.info("[%s] REJECTED by evaluator: %s", name, "; ".join(reasons)[:300])
                return {"failure": f.model_dump(), "stage_records": [rec]}

            rec["evaluator_passed"] = True
            self.records.append(StageRecord(**rec))
            self.write_manifest("running", None)
            log.info("[%s] ok %.0fs cost $%.4f attempts=%d -> %s", name, wall / 1000, cost, meta.attempts, path.name)
            key = STATE_KEY[name]
            out: dict = {f"{key}_path": str(path), f"{key}_sha": sha, "stage_records": [rec]}
            if name == "verify":
                out["status"] = "success" if artifact.verify_pass else "verify_failed"
            return out

        return node

    # ---- producers ------------------------------------------------------------------------

    def _intake(self, state: PipelineState):
        text = Path(state["idea_path"]).read_text()
        brief, meta = intake.produce(idea_text=text, idea_sha=state["idea_sha"], run_id=self.run_id,
                                     idea_id=self.idea_id, caller=self.deps.caller, cfg=self.deps.cfg)
        return brief, meta, []

    def _plan(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p, meta = plan_stage.produce(brief=brief, brief_sha=state["brief_sha"], caller=self.deps.caller,
                                     cfg=self.deps.cfg)
        return p, meta, []

    def _build(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p = None
        parent = state["brief_sha"]
        if state.get("plan_path"):
            p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"],
                               expected_parent=state["brief_sha"])
            parent = state["plan_sha"]
        app_dir = template.materialize(self.deps.template_dir, self.deps.apps_dir, self.run_id)
        result, meta = self.deps.build(app_dir=app_dir, run_dir=self.run_dir, brief=brief, plan=p,
                                       parent_sha=parent, cfg=self.deps.cfg)
        return result, meta, []

    def _verify(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"]) \
            if state.get("plan_path") else None
        build = artifacts.load(Path(state["build_path"]), BuildResult, expected_sha=state["build_sha"])
        t0 = time.monotonic()
        report = self.deps.verify(app_dir=Path(build.app_dir), run_dir=self.run_dir, brief=brief, plan=p,
                                  build_sha=state["build_sha"], cfg=self.deps.cfg)
        return report, CallMeta(model="none", usage=Usage(), cost_reported=0.0,
                                wall_ms=int((time.monotonic() - t0) * 1000)), []

    def _eval_plan(self, p: Plan, state: PipelineState) -> list[str]:
        brief = artifacts.load(Path(state["brief_path"]), Brief)
        return evaluators.evaluate_plan(p, brief)

    def _eval_build(self, r: BuildResult, state: PipelineState) -> list[str]:
        p = artifacts.load(Path(state["plan_path"]), Plan) if state.get("plan_path") else None
        return evaluators.evaluate_build(r, p)

    # ---- terminal nodes ---------------------------------------------------------------------

    def failed(self, state: PipelineState) -> dict:
        f = state.get("failure") or {}
        self.write_manifest("failed", f.get("stage"), finished=True)
        log.info("[run] failed at %s", f.get("stage"))
        return {"status": "failed"}

    def finish(self, state: PipelineState) -> dict:
        status = state.get("status") or "success"
        self.write_manifest(status, None, finished=True)
        log.info("[run] %s", status)
        return {"status": status}


def _route(next_node: str):
    def route(state: PipelineState) -> str:
        return "failed" if state.get("failure") else next_node
    return route


def build_graph(run: _Run, variant: Variant, *, yes: bool):
    g = StateGraph(PipelineState)
    g.add_node("intake", run.stage("intake", run._intake, lambda b, s: evaluators.evaluate_brief(b)))
    if variant == "v1":
        g.add_node("plan", run.stage("plan", run._plan, run._eval_plan))
    g.add_node("build", run.stage("build", run._build, run._eval_build))
    g.add_node("verify", run.stage("verify", run._verify, lambda r, s: evaluators.evaluate_report(r)))
    g.add_node("failed", run.failed)
    g.add_node("finish", run.finish)
    g.add_edge(START, "intake")
    after_intake = "plan" if variant == "v1" else "build"
    g.add_conditional_edges("intake", _route(after_intake), {after_intake: after_intake, "failed": "failed"})
    if variant == "v1":
        g.add_conditional_edges("plan", _route("build"), {"build": "build", "failed": "failed"})
    g.add_conditional_edges("build", _route("verify"), {"verify": "verify", "failed": "failed"})
    g.add_conditional_edges("verify", _route("finish"), {"finish": "finish", "failed": "failed"})
    g.add_edge("failed", END)
    g.add_edge("finish", END)
    return g.compile(checkpointer=InMemorySaver(), interrupt_before=[] if yes else ["build"])


@dataclass
class Handle:
    """A run that may be paused before Build. `next` is LangGraph's next-node tuple."""
    run: _Run
    graph: Any
    config: dict
    outcome: Outcome | None = None

    @property
    def next(self) -> tuple[str, ...]:
        return tuple(self.graph.get_state(self.config).next)

    def _finish(self) -> Outcome:
        m = RunManifest.model_validate_json((self.run.run_dir / "00-manifest.json").read_text())
        self.outcome = Outcome(status=m.status, run_id=self.run.run_id, run_dir=self.run.run_dir, manifest=m)
        return self.outcome

    def summary(self) -> str:
        parts = []
        for fn in ("01-brief.json", "02-plan.json"):
            p = self.run.run_dir / fn
            if p.exists():
                parts.append(f"--- {p.name} ---\n{p.read_text()}")
        return "\n".join(parts)

    def resume(self) -> Outcome:
        self.graph.invoke(None, self.config)
        return self._finish()

    def abort(self) -> Outcome:
        f = StageFailure(stage="build", run_id=self.run.run_id, kind="aborted_by_user",
                         reasons=["declined at the pause before build"])
        artifacts.write(self.run.run_dir, SEQ["build"], "failure", f)
        self.run.write_manifest("aborted", "build", finished=True)
        return self._finish()


def start(*, deps: Deps, variant: Variant, idea_path: Path, idea_id: str, run_id: str, yes: bool) -> Handle:
    run = _Run(deps, variant, run_id, idea_id)
    graph = build_graph(run, variant, yes=yes)
    config = {"configurable": {"thread_id": run_id}}
    state: PipelineState = {
        "run_id": run_id, "graph": variant, "idea_id": idea_id, "idea_path": str(idea_path),
        "idea_sha": artifacts.hash_file(Path(idea_path)), "failure": None, "stage_records": [],
    }
    graph.invoke(state, config)
    h = Handle(run=run, graph=graph, config=config)
    if not h.next:
        h._finish()
    return h


def run(*, deps: Deps, variant: Variant, idea_path: Path, idea_id: str, run_id: str, yes: bool) -> Outcome:
    h = start(deps=deps, variant=variant, idea_path=idea_path, idea_id=idea_id, run_id=run_id, yes=yes)
    if h.outcome is None:
        return h.resume()
    return h.outcome
