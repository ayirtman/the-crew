"""Crew station 6: the UX Designer. Screens and flows, every must-have behavior walked through
real screens. Distinct signal from the UI stage: this is the map, the UI stage picks the tiles."""
from __future__ import annotations

from pathlib import Path

from pipeline import evaluators
from pipeline.config import Config
from pipeline.contracts import AudiencePack, Brief, Plan, UXFlows, UXFlowsDraft
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def produce(*, brief: Brief, plan: Plan, parent_sha: str, caller: StructuredCaller,
            cfg: Config, audience: AudiencePack | None = None) -> tuple[UXFlows, CallMeta]:
    stage = cfg.stages["ux"]
    drop = {"run_id", "parent", "stage", "schema_version"}
    aud = ""
    if audience is not None:
        aud = ("\n\nAUDIENCE RESEARCH (the flows must respect every constraint and pattern):\n"
               + audience.model_dump_json(indent=2, include={"patterns", "constraints"}))
    user = (f"BRIEF:\n{brief.model_dump_json(indent=2, exclude=drop)}{aud}\n\n"
            f"PLAN FILES:\n{plan.model_dump_json(indent=2, include={'files'})}\n\n"
            "Produce the UXFlows. must_have_behaviors are indexed from 0; every index must be "
            "covered by at least one flow, every screen must appear in a flow.")

    def check(draft: UXFlowsDraft) -> list[str]:
        return evaluators.evaluate_uxflows(
            UXFlows(run_id=brief.run_id, parent=parent_sha, **draft.model_dump()), brief)

    res = call_with_retry(caller, system_file=PROMPTS / "ux_system.md", user=user,
                          schema=UXFlowsDraft, stage=stage, attempts=stage.max_attempts, check=check)
    draft: UXFlowsDraft = res.parsed  # type: ignore[assignment]
    flows = UXFlows(run_id=brief.run_id, parent=parent_sha, **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)
    return flows, meta
