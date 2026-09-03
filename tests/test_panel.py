"""The Focus Group, cast per project: seven fixed seats, identities researched from the brief
and the audience pack. The arbiter stays pure Python; the cast is the new signal."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import evaluators
from pipeline.config import load_config
from pipeline.contracts import (Brief, CastingDraft, PersonaReaction, PersonaReactionDraft,
                                ReactionReport, SEATS)
from pipeline.llm import CallResult, MockCaller
from pipeline.stages import panel
from tests.test_audience_casting import AUDIENCE_GOOD, CAST_GOOD

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _brief():
    d = json.loads((FIX / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def _audience():
    from pipeline.contracts import AudiencePack
    return AudiencePack(run_id="r1", parent="s", **AUDIENCE_GOOD)


def _draft(des=4, cla=4, fea=4):
    return {"scores": {"desirability": des, "clarity": cla, "feasibility": fea},
            "objections": ["too many taps to reach the result", "no way to recover from a mistake"],
            "one_change": "put the result inline"}


def _cast():
    return CastingDraft.model_validate(CAST_GOOD).personas


def _reaction(seat, des=4, cla=4, fea=4):
    return PersonaReaction(persona=seat, **_draft(des, cla, fea))


def _reactions(des=4, fea=4):
    return [_reaction(s, des=des, fea=fea) for s in SEATS]


class SeqCaller(MockCaller):
    """Canned drafts per schema; PersonaReactionDraft responses come from a list in call order."""

    def __init__(self, responses, reaction_drafts):
        super().__init__(responses)
        self.reaction_drafts = list(reaction_drafts)
        self.n = 0

    def call(self, *, system_file, user, schema, stage):
        if schema is PersonaReactionDraft:
            self.calls.append({"system_file": system_file, "user": user, "schema": schema,
                               "system_prompt_text": Path(system_file).read_text()})
            d = self.reaction_drafts[self.n]
            self.n += 1
            from pipeline.contracts import Usage
            return CallResult(parsed=schema.model_validate(d), usage=Usage(), cost_reported=0.0,
                              num_turns=1, duration_ms=1, raw={})
        return super().call(system_file=system_file, user=user, schema=schema, stage=stage)


# ---------------------------------------------------------------- arbiter (unchanged rules)


def test_arbitrate_kills_below_mean_desirability():
    rs = _reactions(des=2)
    means, kill, reasons = panel.arbitrate(rs, CFG.panel)
    assert kill is True and any("2.0" in r and "2.5" in r for r in reasons)


def test_arbitrate_kills_on_any_feasibility_at_or_below_one():
    rs = _reactions()
    rs[3] = _reaction(SEATS[3], fea=1)
    means, kill, reasons = panel.arbitrate(rs, CFG.panel)
    assert kill is True and any("feasibility" in r for r in reasons)


def test_boundary_helper_unchanged():
    assert panel.is_boundary(2.33, CFG.panel) is True
    assert panel.is_boundary(4.0, CFG.panel) is False


# ---------------------------------------------------------------- casting + stage


def test_stage_casts_then_collects_seven_reactions(tmp_path):
    caller = SeqCaller({CastingDraft: CAST_GOOD}, [_draft()] * 7)
    rep, meta = panel.produce(brief=_brief(), evidence=None, audience=_audience(),
                              parent_sha="sha256:aud", caller=caller, cfg=CFG)
    assert [r.persona for r in rep.reactions] == list(SEATS)
    assert len(rep.cast) == 7 and rep.kill is False and rep.parent == "sha256:aud"
    # each persona call carries its cast identity, and the end_user's constraints
    first = [c for c in caller.calls if c["schema"] is PersonaReactionDraft][0]["system_prompt_text"]
    assert "Persona 0" in first and "cannot read" in first


def test_casting_prompt_carries_audience_constraints(tmp_path):
    caller = SeqCaller({CastingDraft: CAST_GOOD}, [_draft()] * 7)
    panel.produce(brief=_brief(), evidence=None, audience=_audience(), parent_sha="s",
                  caller=caller, cfg=CFG)
    cast_call = [c for c in caller.calls if c.get("schema") is CastingDraft]
    assert cast_call and "cannot read" in cast_call[0]["user"]


def test_stage_kill_flows_from_scores(tmp_path):
    caller = SeqCaller({CastingDraft: CAST_GOOD}, [_draft(des=0, fea=1)] * 7)
    rep, _ = panel.produce(brief=_brief(), evidence=None, audience=_audience(), parent_sha="s",
                           caller=caller, cfg=CFG)
    assert rep.kill is True and rep.kill_reasons


def test_stage_confirms_at_the_boundary_with_a_second_sample(tmp_path):
    # first sample mean 2.43 (boundary: 4 seats at 2, 3 at 3) -> seven more calls
    first = [_draft(des=2)] * 4 + [_draft(des=3)] * 3
    second = [_draft(des=3)] * 7
    caller = SeqCaller({CastingDraft: CAST_GOOD}, first + second)
    rep, _ = panel.produce(brief=_brief(), evidence=None, audience=_audience(), parent_sha="s",
                           caller=caller, cfg=CFG)
    assert len(rep.reactions) == 14
    assert rep.kill is False  # mean over 14 = (17+21)/14 = 2.71


def test_stage_far_from_boundary_stays_seven_calls(tmp_path):
    caller = SeqCaller({CastingDraft: CAST_GOOD}, [_draft(des=4)] * 7)
    rep, _ = panel.produce(brief=_brief(), evidence=None, audience=_audience(), parent_sha="s",
                           caller=caller, cfg=CFG)
    assert len(rep.reactions) == 7


# ---------------------------------------------------------------- evaluator


def test_evaluate_reaction_rejects_tampered_kill():
    rs = _reactions(des=1)
    means, kill, reasons = panel.arbitrate(rs, CFG.panel)
    rep = ReactionReport(run_id="r1", parent="s", cast=_cast(), reactions=rs, means=means,
                         kill=False, kill_reasons=[])
    assert any("kill" in r for r in evaluators.evaluate_reaction(rep, CFG.panel))


def test_evaluator_rejects_unconfirmed_boundary_verdict():
    rs = [_reaction(s, des=2) for s in SEATS[:4]] + [_reaction(s, des=3) for s in SEATS[4:]]
    means, kill, reasons = panel.arbitrate(rs, CFG.panel)
    rep = ReactionReport(run_id="r1", parent="s", cast=_cast(), reactions=rs, means=means,
                         kill=kill, kill_reasons=reasons)
    assert any("boundary" in r for r in evaluators.evaluate_reaction(rep, CFG.panel))
