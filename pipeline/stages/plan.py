"""Stage 3 (v1 only). Brief in, Plan out. Kept as small as it can be: sequential planning is the
task shape where every multi-agent variant got worse, so this is one call and a short object."""
from __future__ import annotations

from pathlib import Path

from pipeline.budget import BudgetExceeded
from pipeline.config import Config
from pipeline.contracts import Brief, BudgetSnapshot, Plan, PlanDraft
from pipeline import evaluators
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"

# Not LLM-authored. The template's config surface is off limits and the build has one shape.
CONSTRAINTS = [
    "Do not edit package.json, package-lock.json, tsconfig.json, next.config.ts, eslint.config.mjs or vitest.config.mts.",
    "Do not add dependencies. Everything needed is installed.",
    "One page at app/page.tsx, one API route under app/api/, pure logic under lib/, tests under tests/.",
    "Every acceptance criterion is one vitest test whose title is exactly the criterion's test_name.",
    "No dev server, no git, no network calls in tests.",
]


def produce(*, brief: Brief, brief_sha: str, caller: StructuredCaller, cfg: Config) -> tuple[Plan, CallMeta]:
    stage = cfg.stages["plan"]
    brief_json = brief.model_dump_json(indent=2, exclude={"run_id", "parent", "stage", "schema_version"})
    user = (
        f"BRIEF:\n{brief_json}\n\nCONSTRAINTS:\n" + "\n".join(f"- {c}" for c in CONSTRAINTS)
        + "\n\nProduce the Plan. must_have_behaviors are indexed from 0; give each exactly one acceptance criterion."
    )
    if stage.max_input_chars is not None and len(user) > stage.max_input_chars:
        raise BudgetExceeded(f"plan input is {len(user)} chars, cap {stage.max_input_chars}",
                             BudgetSnapshot(tokens_used=len(user) // 3, tokens_cap=stage.max_input_chars // 3))
    def check(draft: PlanDraft) -> list[str]:
        return evaluators.evaluate_plan(Plan(run_id=brief.run_id, parent=brief_sha, constraints=CONSTRAINTS,
                                             **draft.model_dump()), brief)

    res = call_with_retry(caller, system_file=PROMPTS / "plan_system.md", user=user, schema=PlanDraft,
                          stage=stage, attempts=stage.max_attempts, check=check)
    draft: PlanDraft = res.parsed  # type: ignore[assignment]
    plan = Plan(run_id=brief.run_id, parent=brief_sha, constraints=CONSTRAINTS, **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)
    return plan, meta
