"""Stage 2, caged and cast. Seven fixed seats; each seat's identity is cast per project from
the brief and the research, then reacts in one structured call. The arbiter is pure Python
with a written kill rule; the model's scores are inputs, never verdicts."""
from __future__ import annotations

import tempfile
from pathlib import Path

from pipeline.config import Config, PanelRules
from pipeline.contracts import (AudiencePack, Brief, CastingDraft, DomainPack, EvidencePack, MeanScores,
                                PersonaReaction, PersonaReactionDraft, PersonaSpec, ReactionReport,
                                Usage)
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


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
    """A mean within the margin of the bar is inside sampling noise."""
    return abs(mean_desirability - rules.min_mean_desirability) < rules.confirm_margin


def _render_cast(spec: PersonaSpec) -> Path:
    text = (PROMPTS / "persona_cast.md").read_text()
    text = (text.replace("{{NAME}}", spec.name)
                .replace("{{SEAT}}", spec.seat)
                .replace("{{DESCRIPTION}}", spec.description)
                .replace("{{CONSTRAINTS}}", "\n".join(f"- {c}" for c in spec.constraints) or "- none")
                .replace("{{GROUNDED}}", "\n".join(f"- {g}" for g in spec.grounded_in)))
    f = tempfile.NamedTemporaryFile("w", suffix=f"-{spec.seat}.md", delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


def produce(*, brief: Brief, evidence: EvidencePack | None, audience: AudiencePack | None,
            parent_sha: str, caller: StructuredCaller, cfg: Config,
            domain: DomainPack | None = None,
            platform: str | None = None) -> tuple[ReactionReport, CallMeta]:
    stage = cfg.stages["panel"]
    drop = {"run_id", "parent", "stage", "schema_version"}
    brief_json = brief.model_dump_json(indent=2, exclude=drop)
    research = ""
    if evidence is not None:
        research += "\n\nEVIDENCE:\n" + evidence.model_dump_json(indent=2, include={"claims", "competitors"})
    if audience is not None:
        research += "\n\nAUDIENCE RESEARCH:\n" + audience.model_dump_json(
            indent=2, include={"patterns", "constraints"})
    if domain is not None:
        research += "\n\nDOMAIN RESEARCH (what the field demands):\n" + domain.model_dump_json(
            indent=2, include={"non_negotiables"})
    if platform:
        research += ("\n\nPLATFORM GUARANTEES (the build system already enforces these; do not "
                     "object to them as missing):\n" + platform)

    usage = Usage()
    cost, wall, turns, attempts = 0.0, 0, 0, 0

    def add(res) -> None:
        nonlocal usage, cost, wall, turns, attempts
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

    # --- casting: this project's seven panelists, grounded in the research
    cast_user = (f"BRIEF:\n{brief_json}{research}\n\n"
                 "Cast the focus group: exactly one persona per seat. Ground every persona in "
                 "the brief or the research; the end_user's constraints must include every "
                 "audience constraint that applies to them.")
    res = call_with_retry(caller, system_file=PROMPTS / "casting_system.md", user=cast_user,
                          schema=CastingDraft, stage=stage, attempts=stage.max_attempts)
    add(res)
    cast = list(res.parsed.personas)  # type: ignore[attr-defined]

    react_user = f"BRIEF:\n{brief_json}{research}\n\nReact as your persona. Score honestly."
    reactions: list[PersonaReaction] = []

    def sample() -> None:
        for spec in cast:
            r = call_with_retry(caller, system_file=_render_cast(spec), user=react_user,
                                schema=PersonaReactionDraft, stage=stage, attempts=stage.max_attempts)
            add(r)
            reactions.append(PersonaReaction(persona=spec.seat, **r.parsed.model_dump()))

    sample()
    means, kill, reasons = arbitrate(reactions, cfg.panel)
    if is_boundary(means.desirability, cfg.panel):
        # inside sampling noise: one confirmation sample, verdict from both
        sample()
        means, kill, reasons = arbitrate(reactions, cfg.panel)

    report = ReactionReport(run_id=brief.run_id, parent=parent_sha, cast=cast, reactions=reactions,
                            means=means, kill=kill, kill_reasons=reasons)
    meta = CallMeta(model=stage.model or "haiku", usage=usage, cost_reported=cost, wall_ms=wall,
                    num_turns=turns, attempts=attempts)
    return report, meta
