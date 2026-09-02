"""The Discovery Interviewer. Human touch #1 becomes a conversation: the machine asks, the
human answers, the machine drafts a sharper contract, the human approves the diff. After a
panel kill, the personas' own objections become the questions. The bar never moves."""
from __future__ import annotations

from pathlib import Path

from pipeline.config import Config
from pipeline.contracts import (IdeaRevisionDraft, InterviewQuestionsDraft, ReactionReport)
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def _meta(res, stage) -> CallMeta:
    return CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)


def questions(*, idea_text: str, caller: StructuredCaller, cfg: Config,
              panel: ReactionReport | None = None):
    stage = cfg.stages["interview"]
    user = f"IDEA:\n\n{idea_text.strip()}\n\n"
    if panel is not None:
        obj = "\n".join(f"- [{r.persona}] {o}" for r in panel.reactions for o in r.objections)
        user += ("THE PANEL KILLED THIS IDEA.\nKill reasons:\n"
                 + "\n".join(f"- {k}" for k in panel.kill_reasons)
                 + f"\nObjections:\n{obj}\n\n"
                 "Turn the strongest objections into questions only the idea's owner can answer.")
    else:
        user += ("First contact: interview the idea's owner before anything runs. Ask what a "
                 "senior agency strategist would ask: who exactly, why now, why not the "
                 "closest alternative, what is the wedge.")
    res = call_with_retry(caller, system_file=PROMPTS / "interview_system.md", user=user,
                          schema=InterviewQuestionsDraft, stage=stage, attempts=stage.max_attempts)
    return res.parsed, _meta(res, stage)


def revise(*, idea_text: str, qa: list[tuple[str, str]], caller: StructuredCaller, cfg: Config):
    stage = cfg.stages["interview"]
    transcript = "\n".join(f"Q: {q}\nA: {a}" for q, a in qa)
    user = (f"CURRENT IDEA FILE:\n\n{idea_text.strip()}\n\nINTERVIEW:\n{transcript}\n\n"
            "Fold the answers into a revised idea: prose (the owner's idea, sharpened, never "
            "invented), musts and nevers (keep every existing one unless an answer explicitly "
            "changed it), and a change_note saying what moved and why.")
    res = call_with_retry(caller, system_file=PROMPTS / "revise_system.md", user=user,
                          schema=IdeaRevisionDraft, stage=stage, attempts=stage.max_attempts)
    return res.parsed, _meta(res, stage)
