"""Stage 1b. How this audience actually interacts: mistake handling for people who cannot read,
attention spans, what distracts. The research the UX, UI and build stations read before anything
is drawn. Same anti-fabrication oracles as evidence: real searches, live URLs."""
from __future__ import annotations

from pathlib import Path

from pipeline.config import Config
from pipeline.contracts import AudiencePack, AudiencePackDraft, Brief
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta
from pipeline.stages.evidence import default_fetch, search_count

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def produce(*, brief: Brief, parent_sha: str, caller: StructuredCaller, cfg: Config,
            fetch=default_fetch) -> tuple[AudiencePack, CallMeta]:
    from pipeline import evaluators

    stage = cfg.stages["audience"]
    user = (
        f"PRODUCT:\ntitle: {brief.title}\ntarget user: {brief.target_user}\n"
        f"feature: {brief.single_feature}\nui elements: {', '.join(brief.ui_elements)}\n\n"
        "Research how THIS audience actually interacts with software. Especially: how they "
        "handle mistakes and feedback, what they can and cannot do (reading, motor control, "
        "attention span), and what distracts or confuses them."
    )

    def check(draft: AudiencePackDraft) -> list[str]:
        pack = AudiencePack(run_id=brief.run_id, parent=parent_sha, web_search_requests=1,
                            **draft.model_dump())
        return evaluators.evaluate_audience(pack, cfg.audience, fetch=fetch)

    res = call_with_retry(caller, system_file=PROMPTS / "audience_system.md", user=user,
                          schema=AudiencePackDraft, stage=stage, attempts=stage.max_attempts,
                          check=check)
    draft: AudiencePackDraft = res.parsed  # type: ignore[assignment]
    pack = AudiencePack(run_id=brief.run_id, parent=parent_sha,
                        web_search_requests=search_count(res.raw), **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)
    return pack, meta
