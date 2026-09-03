"""The thin supervisor. Routes, holds the budget, writes artifacts. Never reasons about the product.

Two variants share every node: v0 = intake -> build -> verify, v1 = intake -> plan -> build -> verify.
State carries paths and hashes, not objects; every node reloads its input from disk and verifies it,
which is what makes a cascade traceable when something goes wrong.
"""
from __future__ import annotations

import datetime as dt
import functools
import json
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
from pipeline.contracts import (AudiencePack, Brief, BuildResult, DesignSpec, EvidencePack, Plan, ReviewReport,
                                RunManifest, SecurityReport, ShipRecord, StageFailure, StageRecord,
                                TechSpec, TestReport, Usage, UXFlows)
from pipeline.idea import parse_idea
from pipeline.llm import CallerError, SchemaInvalid, StructuredCaller
from pipeline.stages import (CallMeta, analytics as analytics_stage, architect as architect_stage,
                             audience as audience_stage,
                             design as design_stage, evidence as evidence_stage, intake,
                             panel as panel_stage, plan as plan_stage, release as release_stage,
                             review as review_stage, security as security_stage, template,
                             ux as ux_stage)
from pipeline.stages.build import BuilderError
from pipeline.variants import VARIANTS, expand

log = logging.getLogger("pipeline")

Variant = str
# stage name -> artifact name (file stem) and state key prefix
ARTIFACT_SPLIT = True
ARTIFACT = {"intake": "brief", "evidence": "evidence", "audience": "audience", "panel": "panel", "plan": "plan",
            "design": "design", "build": "build", "build_split": "build", "repair": "repair",
            "verify": "verify", "verify2": "verify", "architect": "techspec", "ux": "ux",
            "ui": "design", "review": "review", "review2": "review", "security": "security",
            "security2": "security", "ship": "ship", "analytics": "analytics"}
STATE_KEY = {"intake": "brief", "evidence": "evidence", "audience": "audience", "panel": "panel", "plan": "plan",
             "design": "design", "build": "build", "build_split": "build", "repair": "repair",
             "verify": "report", "verify2": "report2", "architect": "techspec", "ux": "ux",
             "ui": "design", "review": "review", "review2": "review2", "security": "security",
             "security2": "security2", "ship": "ship", "analytics": "analytics"}
# a second-pass or aliased node bills and budgets as its base stage
BASE_STAGE = {"verify2": "verify", "review2": "review", "security2": "security", "ui": "design"}


@dataclass
class Deps:
    cfg: Config
    caller: StructuredCaller
    build: Callable[..., tuple[BuildResult, CallMeta]]
    verify: Callable[..., TestReport]
    template_dir: Path
    apps_dir: Path
    runs_dir: Path
    repair: Callable[..., tuple[BuildResult, CallMeta]] | None = None
    fetch: Callable[[str], int] | None = None
    build_split: Callable | None = None  # override for tests; None = the real parallel split stage
    audit: Callable | None = None    # npm audit runner for the security stage (None = real)
    deploy: Callable | None = None   # vercel runner for the ship stage (None = real)
    probe: Callable | None = None    # url probe for the analytics stage (None = real)


class PipelineState(TypedDict, total=False):
    run_id: str
    graph: Variant
    idea_id: str
    idea_path: str
    idea_sha: str
    brief_path: str
    brief_sha: str
    evidence_path: str
    evidence_sha: str
    audience_path: str
    audience_sha: str
    panel_path: str
    panel_sha: str
    plan_path: str
    plan_sha: str
    design_path: str
    design_sha: str
    techspec_path: str
    techspec_sha: str
    ux_path: str
    ux_sha: str
    review_path: str
    review_sha: str
    review2_path: str
    review2_sha: str
    security_path: str
    security_sha: str
    security2_path: str
    security2_sha: str
    ship_path: str
    ship_sha: str
    analytics_path: str
    analytics_sha: str
    review_ok: bool
    verify_ok: bool
    security_ok: bool
    repair_path: str
    repair_sha: str
    report2_path: str
    report2_sha: str
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
        self.nodes = expand(variant)
        self.seq = {name: i + 1 for i, name in enumerate(self.nodes)}
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
            stages=list(self.records), variant_stages=list(VARIANTS[self.variant]),
            config_snapshot=self.deps.cfg.snapshot(),
            pipeline_git_sha=_git_sha(), template_version=self.template_version,
            claude_code_version=_claude_version(),
        )
        (self.run_dir / "00-manifest.json").write_text(m.model_dump_json(indent=2))
        return m

    # ---- the stage wrapper -------------------------------------------------------------

    def stage(self, name: str, produce: Callable[[PipelineState], tuple[Any, CallMeta, list[str]]],
              evaluate: Callable[[Any, PipelineState], list[str]]) -> Callable[[PipelineState], dict]:
        seq = self.seq[name]

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
                out = {"failure": f.model_dump(), "stage_records": [rec]}
                if name == "repair":
                    # a dead repair does not erase the run: the first verify's verdict stands
                    out["status"] = "verify_failed"
                return out

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
                rejected = None
                if e.draft is not None:
                    rp = self.run_dir / f"{seq:02d}-{ARTIFACT[name]}.rejected.json"
                    rp.write_text(json.dumps(e.draft, indent=2))
                    rejected = str(rp)
                return fail(e.kind, e.reasons, rejected=rejected)
            except BudgetExceeded as e:
                return fail("budget_exceeded", [e.reason], budget=e.snapshot)
            except (CallerError, BuilderError) as e:
                return fail("subprocess_error", [str(e)])

            path, sha = artifacts.write(self.run_dir, seq, ARTIFACT[name], artifact)
            cost = meta.cost_reported if meta.cost_reported else cost_for(meta.usage, meta.model, self.deps.cfg)
            # subscription stages report a notional cost and bill nothing; api_key stages bill what they report
            base = BASE_STAGE.get(name, name)
            stage_cfg = self.deps.cfg.stages.get(base)
            billed = cost if (stage_cfg and stage_cfg.auth == "api_key") else 0.0
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
            if name in ("verify", "verify2"):
                out["status"] = "success" if artifact.verify_pass else "verify_failed"
                out["verify_ok"] = artifact.verify_pass
            if name in ("review", "review2"):
                out["review_ok"] = artifact.review_pass
            if name in ("security", "security2"):
                # the three red boxes speak as one: all pass or the run repairs / stays failed
                ok = artifact.security_pass and state.get("review_ok", True) and state.get("verify_ok", True)
                out["security_ok"] = artifact.security_pass
                out["status"] = "success" if ok else "verify_failed"
            if name == "ship":
                out["status"] = "success"
            if name == "panel" and getattr(artifact, "kill", False):
                out["status"] = "killed"
            return out

        return node

    # ---- producers ------------------------------------------------------------------------

    def _intake(self, state: PipelineState):
        text = Path(state["idea_path"]).read_text()
        brief, meta = intake.produce(idea_text=text, idea_sha=state["idea_sha"], run_id=self.run_id,
                                     idea_id=self.idea_id, caller=self.deps.caller, cfg=self.deps.cfg)
        return brief, meta, []

    def _evidence(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        kw = {"fetch": self.deps.fetch} if self.deps.fetch else {}
        pack, meta = evidence_stage.produce(brief=brief, parent_sha=state["brief_sha"],
                                            caller=self.deps.caller, cfg=self.deps.cfg, **kw)
        return pack, meta, []

    def _panel(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        ev = self._load_evidence(state)
        aud = self._load_audience(state)
        parent = state.get("audience_sha") or state.get("evidence_sha") or state["brief_sha"]
        rep, meta = panel_stage.produce(brief=brief, evidence=ev, audience=aud, parent_sha=parent,
                                        caller=self.deps.caller, cfg=self.deps.cfg)
        return rep, meta, []

    def _audience(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        parent = state.get("evidence_sha") or state["brief_sha"]
        kw = {"fetch": self.deps.fetch} if self.deps.fetch else {}
        pack, meta = audience_stage.produce(brief=brief, parent_sha=parent, caller=self.deps.caller,
                                            cfg=self.deps.cfg, **kw)
        return pack, meta, []

    def _load_audience(self, state: PipelineState) -> AudiencePack | None:
        if state.get("audience_path"):
            return artifacts.load(Path(state["audience_path"]), AudiencePack,
                                  expected_sha=state["audience_sha"])
        return None

    def _load_evidence(self, state: PipelineState) -> EvidencePack | None:
        if state.get("evidence_path"):
            return artifacts.load(Path(state["evidence_path"]), EvidencePack,
                                  expected_sha=state["evidence_sha"])
        return None

    def _plan(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p, meta = plan_stage.produce(brief=brief, brief_sha=state["brief_sha"], caller=self.deps.caller,
                                     cfg=self.deps.cfg, evidence=self._load_evidence(state))
        return p, meta, []

    def _design(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        spec, meta = design_stage.produce(
            brief=brief, evidence=self._load_evidence(state), parent_sha=state["plan_sha"],
            components=design_stage.load_components(self.deps.template_dir),
            caller=self.deps.caller, cfg=self.deps.cfg)
        return spec, meta, []

    def _build_split(self, state: PipelineState):
        from pipeline.stages import build_split as split_stage

        producer = self.deps.build_split or split_stage.produce
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"]) \
            if state.get("plan_path") else None
        d = artifacts.load(Path(state["design_path"]), DesignSpec, expected_sha=state["design_sha"]) \
            if state.get("design_path") else None
        parent = state.get("design_sha") or state.get("plan_sha") or state["brief_sha"]
        app_dir = template.materialize(self.deps.template_dir, self.deps.apps_dir, self.run_id)
        result, meta = producer(
            app_dir=app_dir, run_dir=self.run_dir, brief=brief, plan=p, design=d, parent_sha=parent,
            cfg=self.deps.cfg, artifact_prefix=f"{self.seq['build_split']:02d}-build",
            audience=self._load_audience(state))
        return result, meta, []

    def _build(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p = None
        parent = state["brief_sha"]
        if state.get("plan_path"):
            p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"],
                               expected_parent=state["brief_sha"])
            parent = state["plan_sha"]
        d = None
        if state.get("design_path"):
            d = artifacts.load(Path(state["design_path"]), DesignSpec, expected_sha=state["design_sha"])
            parent = state["design_sha"]
        app_dir = template.materialize(self.deps.template_dir, self.deps.apps_dir, self.run_id)
        result, meta = self.deps.build(app_dir=app_dir, run_dir=self.run_dir, brief=brief, plan=p,
                                       parent_sha=parent, cfg=self.deps.cfg, design=d,
                                       artifact_prefix=f"{self.seq['build']:02d}-build")
        return result, meta, []

    def _verify_from(self, state: PipelineState, build_key: str):
        from pipeline.contracts import SplitBuildResult

        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"]) \
            if state.get("plan_path") else None
        model = SplitBuildResult if "build_split" in self.nodes and build_key == "build" else BuildResult
        build = artifacts.load(Path(state[f"{build_key}_path"]), model,
                               expected_sha=state[f"{build_key}_sha"])
        t0 = time.monotonic()
        report = self.deps.verify(app_dir=Path(build.app_dir), run_dir=self.run_dir, brief=brief, plan=p,
                                  build_sha=state[f"{build_key}_sha"], cfg=self.deps.cfg)
        return report, CallMeta(model="none", usage=Usage(), cost_reported=0.0,
                                wall_ms=int((time.monotonic() - t0) * 1000)), []

    def _verify(self, state: PipelineState):
        return self._verify_from(state, "build")

    def _verify2(self, state: PipelineState):
        return self._verify_from(state, "repair")

    def _repair(self, state: PipelineState):
        from pipeline.contracts import SplitBuildResult

        report = artifacts.load(Path(state["report_path"]), TestReport, expected_sha=state["report_sha"])
        model = SplitBuildResult if "build_split" in self.nodes else BuildResult
        loaded = artifacts.load(Path(state["build_path"]), model, expected_sha=state["build_sha"])
        build = loaded.parts[0].model_copy(update={
            "app_dir": loaded.app_dir, "files_written": loaded.files_written}) \
            if isinstance(loaded, SplitBuildResult) else loaded
        kw: dict = {}
        if state.get("review_path"):
            rv = artifacts.load(Path(state["review_path"]), ReviewReport, expected_sha=state["review_sha"])
            if not rv.review_pass:
                kw["review"] = rv
        if state.get("security_path"):
            sec = artifacts.load(Path(state["security_path"]), SecurityReport,
                                 expected_sha=state["security_sha"])
            if not sec.security_pass:
                kw["security"] = sec
        result, meta = self.deps.repair(
            app_dir=Path(build.app_dir), run_dir=self.run_dir, report=report, build_result=build,
            parent_sha=state["report_sha"], cfg=self.deps.cfg,
            artifact_prefix=f"{self.seq['repair']:02d}-repair", **kw)
        return result, meta, []

    def _eval_plan(self, p: Plan, state: PipelineState) -> list[str]:
        brief = artifacts.load(Path(state["brief_path"]), Brief)
        return evaluators.evaluate_plan(p, brief)

    def _eval_build(self, r: BuildResult, state: PipelineState) -> list[str]:
        # with a review stage downstream, planned-file coverage is review's check (repair can fix
        # it there); double-gating at build killed full runs over one missing test file
        p = None if "review" in self.nodes else (
            artifacts.load(Path(state["plan_path"]), Plan) if state.get("plan_path") else None)
        return evaluators.evaluate_build(r, p)

    def _eval_split(self, r, state: PipelineState) -> list[str]:
        p = None if "review" in self.nodes else (
            artifacts.load(Path(state["plan_path"]), Plan) if state.get("plan_path") else None)
        return evaluators.evaluate_split_build(r, p)

    def _architect(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"])
        spec, meta = architect_stage.produce(brief=brief, plan=p, parent_sha=state["plan_sha"],
                                             caller=self.deps.caller, cfg=self.deps.cfg)
        return spec, meta, []

    def _ux(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"])
        flows, meta = ux_stage.produce(brief=brief, plan=p, parent_sha=state["techspec_sha"],
                                       caller=self.deps.caller, cfg=self.deps.cfg,
                                       audience=self._load_audience(state))
        return flows, meta, []

    def _ui(self, state: PipelineState):
        brief = artifacts.load(Path(state["brief_path"]), Brief, expected_sha=state["brief_sha"])
        uxf = artifacts.load(Path(state["ux_path"]), UXFlows, expected_sha=state["ux_sha"])
        spec, meta = design_stage.produce(
            brief=brief, evidence=self._load_evidence(state), parent_sha=state["ux_sha"],
            components=design_stage.load_components(self.deps.template_dir),
            caller=self.deps.caller, cfg=self.deps.cfg, ux=uxf,
            audience=self._load_audience(state))
        return spec, meta, []

    def _load_build(self, state: PipelineState, key: str = "build"):
        from pipeline.contracts import SplitBuildResult
        model = SplitBuildResult if "build_split" in self.nodes and key == "build" else BuildResult
        return artifacts.load(Path(state[f"{key}_path"]), model, expected_sha=state[f"{key}_sha"])

    def _program_meta(self, t0: float) -> CallMeta:
        return CallMeta(model="none", usage=Usage(), cost_reported=0.0,
                        wall_ms=int((time.monotonic() - t0) * 1000))

    def _review_from(self, state: PipelineState, build_key: str):
        t0 = time.monotonic()
        build = self._load_build(state, build_key)
        p = artifacts.load(Path(state["plan_path"]), Plan, expected_sha=state["plan_sha"]) \
            if state.get("plan_path") else None
        ts = artifacts.load(Path(state["techspec_path"]), TechSpec, expected_sha=state["techspec_sha"]) \
            if state.get("techspec_path") else None
        rep = review_stage.produce(app_dir=Path(build.app_dir), template_dir=Path(self.deps.template_dir),
                                   build=build, plan=p, techspec=ts,
                                   parent_sha=state[f"{build_key}_sha"], run_id=self.run_id)
        return rep, self._program_meta(t0), []

    def _review(self, state: PipelineState):
        return self._review_from(state, "build")

    def _review2(self, state: PipelineState):
        # the repair artifact carries no overlap attribution; disk-state checks re-run in full
        return self._review_from(state, "repair")

    def _security_from(self, state: PipelineState, parent_key: str):
        import subprocess as _sp
        t0 = time.monotonic()
        build = self._load_build(state)
        rep = security_stage.produce(app_dir=Path(build.app_dir), template_dir=Path(self.deps.template_dir),
                                     cfg=self.deps.cfg, parent_sha=state[parent_key], run_id=self.run_id,
                                     runner=self.deps.audit or _sp.run)
        return rep, self._program_meta(t0), []

    def _security(self, state: PipelineState):
        return self._security_from(state, "report_sha")

    def _security2(self, state: PipelineState):
        return self._security_from(state, "report2_sha")

    def _ship(self, state: PipelineState):
        import subprocess as _sp
        t0 = time.monotonic()
        build = self._load_build(state)
        parent = state.get("security2_sha") or state["security_sha"]
        rec = release_stage.produce(app_dir=Path(build.app_dir), parent_sha=parent, run_id=self.run_id,
                                    cfg=self.deps.cfg, runner=self.deps.deploy or _sp.run)
        return rec, self._program_meta(t0), []

    def _analytics(self, state: PipelineState):
        t0 = time.monotonic()
        rec = artifacts.load(Path(state["ship_path"]), ShipRecord, expected_sha=state["ship_sha"])
        rep = analytics_stage.produce(url=rec.url, parent_sha=state["ship_sha"], run_id=self.run_id,
                                      cfg=self.deps.cfg, probe=self.deps.probe)
        return rep, self._program_meta(t0), []

    # ---- terminal nodes ---------------------------------------------------------------------

    def _not_implemented(self, name: str):
        def producer(state: PipelineState):
            raise BuilderError(f"stage '{name}' is not implemented yet")
        return producer

    def killed(self, state: PipelineState) -> dict:
        self.write_manifest("killed", None, finished=True)
        log.info("[run] killed by the panel")
        return {"status": "killed"}

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


def _route_for(name: str, nxt: str, nodes: tuple[str, ...]):
    crew = "security" in nodes

    def route(state: PipelineState) -> str:
        if state.get("failure"):
            if name == "repair":
                return "finish"
            return "failed"
        if name == "panel" and state.get("status") == "killed":
            return "killed"
        if crew:
            # the three red boxes run in sequence and speak after security: all green -> ship,
            # anything red -> one repair pass (first time) or the end (second time)
            if name == "security":
                if state.get("status") == "success":
                    return "ship" if "ship" in nodes else "finish"
                return nxt  # repair is next in line
            if name == "security2":
                if state.get("status") == "success":
                    return "ship" if "ship" in nodes else "finish"
                return "finish"
        else:
            if name == "verify" and "repair" in nodes:
                return "finish" if state.get("status") == "success" else "repair"
            if name == "verify2":
                return "finish"
        return nxt
    return route


def build_graph(run: _Run, variant: Variant, *, yes: bool):
    nodes = run.nodes
    producers = {
        "intake": run._intake, "evidence": run._evidence, "audience": run._audience,
        "panel": run._panel, "plan": run._plan,
        "design": run._design, "build": run._build, "build_split": run._build_split,
        "verify": run._verify, "verify2": run._verify2, "repair": run._repair,
        "architect": run._architect, "ux": run._ux, "ui": run._ui,
        "review": run._review, "review2": run._review2,
        "security": run._security, "security2": run._security2,
        "ship": run._ship, "analytics": run._analytics,
    }
    evals: dict[str, Callable] = {
        "intake": lambda b, s: evaluators.evaluate_brief(b, parse_idea(Path(s["idea_path"]).read_text())),
        "plan": run._eval_plan,
        "build": run._eval_build,
        "verify": lambda r, s: evaluators.evaluate_report(r),
        "verify2": lambda r, s: evaluators.evaluate_report(r),
        "repair": lambda r, s: evaluators.evaluate_build(r, None),
        "evidence": lambda e, s: evaluators.evaluate_evidence(
            e, run.deps.cfg.evidence, fetch=run.deps.fetch or evidence_stage.default_fetch),
        "audience": lambda a, s: evaluators.evaluate_audience(
            a, run.deps.cfg.audience, fetch=run.deps.fetch or evidence_stage.default_fetch),
        "panel": lambda r, s: evaluators.evaluate_reaction(r, run.deps.cfg.panel),
        "design": lambda d, s: evaluators.evaluate_design(
            d, artifacts.load(Path(s["brief_path"]), Brief),
            design_stage.load_components(run.deps.template_dir)),
        "build_split": run._eval_split,
        "architect": lambda t, s: evaluators.evaluate_techspec(
            t, artifacts.load(Path(s["plan_path"]), Plan)),
        "ux": lambda u, s: evaluators.evaluate_uxflows(u, artifacts.load(Path(s["brief_path"]), Brief)),
        "ui": lambda d, s: evaluators.evaluate_design(
            d, artifacts.load(Path(s["brief_path"]), Brief),
            design_stage.load_components(run.deps.template_dir),
            ux=artifacts.load(Path(s["ux_path"]), UXFlows)),
    }
    g = StateGraph(PipelineState)
    for name in nodes:
        producer = producers.get(name) or run._not_implemented(name)
        ev = evals.get(name) or (lambda a, s: [])
        g.add_node(name, run.stage(name, producer, ev))
    g.add_node("failed", run.failed)
    g.add_node("killed", run.killed)
    g.add_node("finish", run.finish)
    g.add_edge(START, nodes[0])
    following = list(nodes[1:]) + ["finish"]
    for name, nxt in zip(nodes, following):
        targets = {nxt: nxt, "failed": "failed"}
        if name == "panel":
            targets["killed"] = "killed"
        if name == "verify" and "repair" in nodes and "security" not in nodes:
            targets.update({"finish": "finish", "repair": "repair"})
        if name == "repair":
            targets["finish"] = "finish"
        if name == "verify2" and "security" not in nodes:
            targets = {"finish": "finish", "failed": "failed"}
        if name in ("security", "security2"):
            targets["finish"] = "finish"
            if "ship" in nodes:
                targets["ship"] = "ship"
        g.add_conditional_edges(name, _route_for(name, nxt, nodes), targets)
    for t in ("failed", "killed", "finish"):
        g.add_edge(t, END)
    pause = "build_split" if "build_split" in nodes else "build"
    interrupts = [] if yes else [pause]
    if "ship" in nodes:
        # pressing publish is one of the diagram's two human touches; --yes never skips it
        interrupts.append("ship")
    return g.compile(checkpointer=InMemorySaver(), interrupt_before=interrupts)


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
        for p in sorted(self.run.run_dir.glob("0*-*.json")):
            if p.name != "00-manifest.json" and not p.name.endswith((".rejected.json",)) \
                    and p.stem.split("-", 1)[1] in ("brief", "evidence", "audience", "panel", "plan", "techspec",
                                                    "ux", "design"):
                parts.append(f"--- {p.name} ---\n{p.read_text()}")
        return "\n".join(parts)

    def resume(self) -> Outcome | None:
        """Runs to the next interrupt or the end. Returns the Outcome only when finished."""
        self.graph.invoke(None, self.config)
        if self.next:
            return None
        return self._finish()

    def decline_ship(self) -> Outcome:
        """The human said no at publish. A measurement, not a failure: the app is verified."""
        self.run.write_manifest("verified_unshipped", None, finished=True)
        return self._finish()

    def abort(self) -> Outcome:
        f = StageFailure(stage="build", run_id=self.run.run_id, kind="aborted_by_user",
                         reasons=["declined at the pause before build"])
        seq = self.run.seq.get("build") or self.run.seq["build_split"]
        artifacts.write(self.run.run_dir, seq, "failure", f)
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
    """Runs to the end, resuming through every pause (tests and eval only; the CLI prompts)."""
    h = start(deps=deps, variant=variant, idea_path=idea_path, idea_id=idea_id, run_id=run_id, yes=yes)
    while h.outcome is None:
        if not h.next:
            return h._finish()
        h.resume()
    return h.outcome
