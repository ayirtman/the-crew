import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import evaluators
from pipeline.config import load_config
from pipeline.contracts import Brief, PersonaReaction, PersonaReactionDraft, ReactionReport
from pipeline.llm import MockCaller
from pipeline.stages import panel

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _brief():
    d = json.loads((FIX / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def _draft(des=4, cla=4, fea=4):
    return {"scores": {"desirability": des, "clarity": cla, "feasibility": fea},
            "objections": ["too many clicks to reach the result", "no way to correct a typo quickly"],
            "one_change": "put the result inline under the input"}


def _reaction(persona, des=4, cla=4, fea=4):
    return PersonaReaction(persona=persona, **_draft(des, cla, fea))


def test_scores_bounded_zero_to_five():
    with pytest.raises(ValidationError):
        PersonaReactionDraft.model_validate(_draft(des=6))


def test_at_least_two_objections():
    d = _draft()
    d["objections"] = ["only one"]
    with pytest.raises(ValidationError):
        PersonaReactionDraft.model_validate(d)


def test_arbitrate_mean_at_threshold_survives():
    rs = [_reaction("target_user", des=2), _reaction("skeptic", des=3), _reaction("operator", des=2.5 * 2 - 2)]
    means, kill, reasons = panel.arbitrate([_reaction("target_user", des=2), _reaction("skeptic", des=3),
                                            _reaction("operator", des=3)], CFG.panel)
    assert means.desirability == pytest.approx(8 / 3)
    assert kill is False and reasons == []


def test_arbitrate_kills_below_mean_desirability_with_numbers_in_reason():
    means, kill, reasons = panel.arbitrate([_reaction("target_user", des=1), _reaction("skeptic", des=2),
                                            _reaction("operator", des=3)], CFG.panel)
    assert kill is True
    assert any("2.0" in r and "2.5" in r for r in reasons)


def test_arbitrate_kills_on_any_feasibility_at_or_below_one():
    means, kill, reasons = panel.arbitrate([_reaction("target_user"), _reaction("skeptic", fea=1),
                                            _reaction("operator")], CFG.panel)
    assert kill is True and any("feasibility" in r for r in reasons)


def test_report_requires_three_distinct_personas():
    with pytest.raises(ValidationError, match="persona"):
        ReactionReport(run_id="r1", parent="s", reactions=[_reaction("skeptic"), _reaction("skeptic"),
                                                           _reaction("operator")],
                       means=panel.MeanScores(desirability=4, clarity=4, feasibility=4),
                       kill=False, kill_reasons=[])


def test_evaluate_reaction_rejects_tampered_kill():
    rs = [_reaction("target_user", des=1), _reaction("skeptic", des=1), _reaction("operator", des=1)]
    means, kill, reasons = panel.arbitrate(rs, CFG.panel)
    rep = ReactionReport(run_id="r1", parent="s", reactions=rs, means=means, kill=False, kill_reasons=[])
    out = evaluators.evaluate_reaction(rep, CFG.panel)
    assert any("kill" in r for r in out)


def test_stage_makes_three_persona_calls_and_arbitrates(tmp_path):
    caller = MockCaller({PersonaReactionDraft: _draft()})
    rep, meta = panel.produce(brief=_brief(), evidence=None, parent_sha="sha256:brief",
                              caller=caller, cfg=CFG)
    assert [r.persona for r in rep.reactions] == ["target_user", "skeptic", "operator"]
    assert rep.kill is False and rep.parent == "sha256:brief"
    assert len(caller.calls) == 3
    assert "Writers checking competitor article length" in caller.calls[0]["system_prompt_text"]


def test_stage_kill_flows_from_scores(tmp_path):
    caller = MockCaller({PersonaReactionDraft: _draft(des=0, fea=1)})
    rep, _ = panel.produce(brief=_brief(), evidence=None, parent_sha="s", caller=caller, cfg=CFG)
    assert rep.kill is True and rep.kill_reasons


# ---------------------------------------------------------------- boundary confirmation
# Measured 2026-09-02: the same idea scored 2.67 then 2.33 across two runs; one persona-point
# of sampling noise flipped the verdict. A verdict near the bar gets a second sample.


class SeqCaller(MockCaller):
    """Returns drafts from a list, in call order."""

    def __init__(self, drafts):
        super().__init__({})
        self.drafts = list(drafts)

    def call(self, *, system_file, user, schema, stage):
        self.calls.append({"system_file": system_file, "user": user, "schema": schema,
                           "system_prompt_text": Path(system_file).read_text()})
        from pipeline.llm import CallResult
        from pipeline.contracts import Usage
        return CallResult(parsed=schema.model_validate(self.drafts[len(self.calls) - 1]),
                          usage=Usage(), cost_reported=0.0, num_turns=1, duration_ms=1, raw={})


def test_boundary_helper_flags_means_within_one_persona_point():
    assert panel.is_boundary(2.33, CFG.panel) is True
    assert panel.is_boundary(2.67, CFG.panel) is True
    assert panel.is_boundary(2.0, CFG.panel) is False
    assert panel.is_boundary(4.0, CFG.panel) is False


def test_report_accepts_six_reactions_two_per_persona():
    rs = [_reaction(p, des=d) for p in ("target_user", "skeptic", "operator") for d in (2, 3)]
    means, kill, reasons = panel.arbitrate(rs, CFG.panel)
    rep = ReactionReport(run_id="r1", parent="s", reactions=rs, means=means, kill=kill, kill_reasons=reasons)
    assert len(rep.reactions) == 6


def test_report_rejects_lopsided_six():
    rs = [_reaction("target_user")] * 4 + [_reaction("skeptic"), _reaction("operator")]
    with pytest.raises(ValidationError, match="persona"):
        ReactionReport(run_id="r1", parent="s", reactions=rs,
                       means=panel.MeanScores(desirability=4, clarity=4, feasibility=4),
                       kill=False, kill_reasons=[])


def test_stage_confirms_at_the_boundary_with_a_second_sample(tmp_path):
    # first sample means 2.33 (boundary) -> three more calls; verdict from all six (mean 2.5 -> pass)
    first = [_draft(des=2), _draft(des=2), _draft(des=3)]
    second = [_draft(des=3), _draft(des=2), _draft(des=3)]
    caller = SeqCaller(first + second)
    rep, _ = panel.produce(brief=_brief(), evidence=None, parent_sha="s", caller=caller, cfg=CFG)
    assert len(caller.calls) == 6 and len(rep.reactions) == 6
    assert rep.means.desirability == pytest.approx(15 / 6)
    assert rep.kill is False


def test_stage_far_from_boundary_stays_three_calls(tmp_path):
    caller = SeqCaller([_draft(des=4)] * 3)
    rep, _ = panel.produce(brief=_brief(), evidence=None, parent_sha="s", caller=caller, cfg=CFG)
    assert len(caller.calls) == 3 and len(rep.reactions) == 3 and rep.kill is False


def test_evaluator_rejects_unconfirmed_boundary_verdict():
    rs = [_reaction("target_user", des=2), _reaction("skeptic", des=2), _reaction("operator", des=3)]
    means, kill, reasons = panel.arbitrate(rs, CFG.panel)
    rep = ReactionReport(run_id="r1", parent="s", reactions=rs, means=means, kill=kill, kill_reasons=reasons)
    out = evaluators.evaluate_reaction(rep, CFG.panel)
    assert any("boundary" in r for r in out)
