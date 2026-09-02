"""Crew station 5: the Architect. Brief and Plan in, TechSpec out: the data model and the
FE<->BE interface contract the two parallel builders will build against. This is the typed
boundary that makes the split build honest instead of hopeful."""
from __future__ import annotations

from pathlib import Path

from pipeline import evaluators
from pipeline.config import Config
from pipeline.contracts import Brief, Plan, TechSpec, TechSpecDraft
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def produce(*, brief: Brief, plan: Plan, parent_sha: str, caller: StructuredCaller,
            cfg: Config) -> tuple[TechSpec, CallMeta]:
    from pipeline.stages.build_split import SCOPES

    stage = cfg.stages["architect"]
    drop = {"run_id", "parent", "stage", "schema_version"}
    scopes = "\n".join(f"- {role}: {', '.join(pre)}" for role, pre in SCOPES.items())
    user = (f"BRIEF:\n{brief.model_dump_json(indent=2, exclude=drop)}\n\n"
            f"PLAN:\n{plan.model_dump_json(indent=2, exclude=drop)}\n\n"
            f"BUILD SCOPES (backend and frontend build in parallel, in these paths only):\n{scopes}\n\n"
            "Produce the TechSpec. Every interface names one plan file per scope; "
            "the Brief's api route must appear as an interface backed by a file under app/api/.")

    def check(draft: TechSpecDraft) -> list[str]:
        return evaluators.evaluate_techspec(
            TechSpec(run_id=brief.run_id, parent=parent_sha, **draft.model_dump()), plan)

    res = call_with_retry(caller, system_file=PROMPTS / "architect_system.md", user=user,
                          schema=TechSpecDraft, stage=stage, attempts=stage.max_attempts, check=check)
    draft: TechSpecDraft = res.parsed  # type: ignore[assignment]
    spec = TechSpec(run_id=brief.run_id, parent=parent_sha, **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)
    return spec, meta
