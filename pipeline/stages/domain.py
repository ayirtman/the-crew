"""Stage 1c. What the subject matter itself demands: the non-negotiables of the field the
product lives in. Market research finds competitors; audience research finds the user;
this finds what an expert would refuse to ship without."""
from __future__ import annotations

from pathlib import Path

from pipeline.config import Config
from pipeline.contracts import Brief, DomainPack, DomainPackDraft
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta
from pipeline.stages.evidence import default_fetch, search_count

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def produce(*, brief: Brief, parent_sha: str, caller: StructuredCaller, cfg: Config,
            fetch=default_fetch) -> tuple[DomainPack, CallMeta]:
    from pipeline import evaluators

    stage = cfg.stages["domain"]
    user = (
        f"PRODUCT:\ntitle: {brief.title}\nproblem: {brief.problem}\n"
        f"feature: {brief.single_feature}\nbehaviors: {'; '.join(brief.must_have_behaviors)}\n\n"
        "Research the SUBJECT MATTER of this product: what does the field itself demand the "
        "product get right? What would a domain expert refuse to ship without? What belongs "
        "together and must never be separated? What do practitioners know that outsiders miss?"
    )

    def check(draft: DomainPackDraft) -> list[str]:
        pack = DomainPack(run_id=brief.run_id, parent=parent_sha, web_search_requests=1,
                          **draft.model_dump())
        return evaluators.evaluate_domain(pack, cfg.domain, fetch=fetch)

    res = call_with_retry(caller, system_file=PROMPTS / "domain_system.md", user=user,
                          schema=DomainPackDraft, stage=stage, attempts=stage.max_attempts,
                          check=check)
    draft: DomainPackDraft = res.parsed  # type: ignore[assignment]
    pack = DomainPack(run_id=brief.run_id, parent=parent_sha,
                      web_search_requests=search_count(res.raw), **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)
    return pack, meta
