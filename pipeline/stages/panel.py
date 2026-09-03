"""Stage 2, caged. Three fixed personas, one structured call each, no tools, no chat.
The arbiter is pure Python with a written kill rule; the model's scores are inputs, never verdicts."""
from __future__ import annotations

import tempfile
from pathlib import Path

from pipeline.config import Config, PanelRules
from pipeline.contracts import (Brief, EvidencePack, MeanScores, PersonaReaction,
                                PersonaReactionDraft, ReactionReport, Usage)
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
PERSONAS = ("target_user", "skeptic", "operator")


def arbitrate(reactions: list[PersonaReaction], rules: PanelRules) -> tuple[MeanScores, bool, list[str]]:
    means = MeanScores(
        desirability=sum(r.scores.desirability for r in reactions) / len(reactions),
        clarity=sum(r.scores.clarity for r in reactions) / len(reactions),
        feasibility=sum(r.scores.feasibility for r in reactions) / len(reactions),
    )
    reasons: list[str] = []
    if means.desirability < rules.min_mean_desirability:
        reasons.append(f"mean desirability {means.desirability:.1f} is below {rules.min_mean_desirability}")
    for r in reactions:
        if r.scores.feasibility <= rules.kill_feasibility_at_or_below:
            reasons.append(f"{r.persona} scored feasibility {r.scores.feasibility} "
                           f"(kill at or below {rules.kill_feasibility_at_or_below})")
    return means, bool(reasons), reasons


def is_boundary(mean_desirability: float, rules: PanelRules) -> bool:
    """A mean within one persona-point of the bar is inside sampling noise."""
    return abs(mean_desirability - rules.min_mean_desirability) < rules.confirm_margin


def _render_persona(persona: str, brief: Brief) -> Path:
    text = (PROMPTS / f"persona_{persona}.md").read_text().replace("{{TARGET_USER}}", brief.target_user)
    f = tempfile.NamedTemporaryFile("w", suffix=f"-{persona}.md", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def produce(*, brief: Brief, evidence: EvidencePack | None, parent_sha: str,
            caller: StructuredCaller, cfg: Config) -> tuple[ReactionReport, CallMeta]:
    stage = cfg.stages["panel"]
    brief_json = brief.model_dump_json(indent=2, exclude={"run_id", "parent", "stage", "schema_version"})
    ev = ""
    if evidence is not None:
        ev = "\n\nEVIDENCE:\n" + evidence.model_dump_json(indent=2, include={"claims", "competitors"})
    user = f"BRIEF:\n{brief_json}{ev}\n\nReact as your persona. Score honestly."

    reactions: list[PersonaReaction] = []
    usage = Usage()
    cost, wall, turns, attempts = 0.0, 0, 0, 0

    def sample() -> None:
        nonlocal usage, cost, wall, turns, attempts
        for persona in PERSONAS:
            res = call_with_retry(caller, system_file=_render_persona(persona, brief), user=user,
                                  schema=PersonaReactionDraft, stage=stage, attempts=stage.max_attempts)
            draft: PersonaReactionDraft = res.parsed  # type: ignore[assignment]
            reactions.append(PersonaReaction(persona=persona, **draft.model_dump()))
            usage = Usage(
                input_tokens=usage.input_tokens + res.usage.input_tokens,
                output_tokens=usage.output_tokens + res.usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens + res.usage.cache_creation_input_tokens,
                cache_read_input_tokens=usage.cache_read_input_tokens + res.usage.cache_read_input_tokens,
            )
            cost += res.cost_reported
            wall += res.duration_ms
            turns += res.num_turns
            attempts = max(attempts, res.attempts)

    sample()
    means, kill, reasons = arbitrate(reactions, cfg.panel)
    if is_boundary(means.desirability, cfg.panel):
        # inside sampling noise: one confirmation sample, verdict from all six reactions
        sample()
        means, kill, reasons = arbitrate(reactions, cfg.panel)
    report = ReactionReport(run_id=brief.run_id, parent=parent_sha, reactions=reactions,
                            means=means, kill=kill, kill_reasons=reasons)
    meta = CallMeta(model=stage.model or "haiku", usage=usage, cost_reported=cost, wall_ms=wall,
                    num_turns=turns, attempts=attempts)
    return report, meta
