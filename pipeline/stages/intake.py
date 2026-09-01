"""Stage 0. Raw idea prose in, typed Brief out. A better interface, nothing more."""
from __future__ import annotations

from pathlib import Path

from pipeline.budget import BudgetExceeded
from pipeline.config import Config
from pipeline.contracts import Brief, BriefDraft, BudgetSnapshot
from pipeline.llm import StructuredCaller
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def produce(*, idea_text: str, idea_sha: str, run_id: str, idea_id: str,
            caller: StructuredCaller, cfg: Config) -> tuple[Brief, CallMeta]:
    stage = cfg.stages["intake"]
    if stage.max_input_chars is not None and len(idea_text) > stage.max_input_chars:
        raise BudgetExceeded(
            f"intake input is {len(idea_text)} chars, cap {stage.max_input_chars}",
            BudgetSnapshot(tokens_used=len(idea_text) // 3, tokens_cap=stage.max_input_chars // 3))
    user = f"IDEA (id {idea_id}):\n\n{idea_text.strip()}\n\nProduce the Brief."
    res = caller.call(system_file=PROMPTS / "intake_system.md", user=user, schema=BriefDraft, stage=stage)
    draft: BriefDraft = res.parsed  # type: ignore[assignment]
    brief = Brief(run_id=run_id, idea_id=idea_id, parent=idea_sha, **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns)
    return brief, meta
