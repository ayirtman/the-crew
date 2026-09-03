"""The Discovery Interviewer: touch #1 becomes a conversation. Upfront questions sharpen the
idea before any run; after a panel kill, the objections become the questions (max 2 loops).
The user approves every revision as a diff; the bar never moves."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.config import load_config
from pipeline.contracts import IdeaRevisionDraft, InterviewQuestionsDraft, ReactionReport, SEATS
from tests.test_audience_casting import CAST_GOOD
from pipeline.idea import parse_idea, render_idea
from pipeline.llm import MockCaller
from pipeline.stages import interview

CFG = load_config("pipeline.toml")

QUESTIONS_GOOD = {"questions": [
    {"id": "Q1", "question": "Who exactly uses this and how often per week?", "why": "desirability"},
    {"id": "Q2", "question": "Why is this better than the closest free alternative?", "why": "competition"},
    {"id": "Q3", "question": "What single change would make you pay for it?", "why": "wedge"},
]}

REVISION_GOOD = {
    "prose": "A parent-guided language app for toddlers: the parent sits with the child, two photos, a prerecorded parent voice names one, the toddler taps it.",
    "musts": ["the parent can choose the language to learn",
              "accuracy is tracked over days and shown as progress"],
    "nevers": ["no gamification, no rewards"],
    "change_note": "Made the parent an active participant; accuracy became multi-day progress.",
}

IDEA = "a toy idea about counting words on a page.\n\n## Must\n- count words\n\n## Never\n- no accounts\n"


# ---------------------------------------------------------------- contracts


def test_questions_contract_bounds():
    q = InterviewQuestionsDraft.model_validate(QUESTIONS_GOOD)
    assert len(q.questions) == 3
    with pytest.raises(ValidationError, match="questions"):
        InterviewQuestionsDraft.model_validate({"questions": QUESTIONS_GOOD["questions"][:2]})


def test_questions_ids_unique():
    bad = json.loads(json.dumps(QUESTIONS_GOOD))
    bad["questions"][1]["id"] = "Q1"
    with pytest.raises(ValidationError, match="duplicate"):
        InterviewQuestionsDraft.model_validate(bad)


def test_revision_contract_requires_substance():
    r = IdeaRevisionDraft.model_validate(REVISION_GOOD)
    assert r.musts
    with pytest.raises(ValidationError, match="prose"):
        IdeaRevisionDraft.model_validate({**REVISION_GOOD, "prose": "short"})
    with pytest.raises(ValidationError, match="musts"):
        IdeaRevisionDraft.model_validate({**REVISION_GOOD, "musts": []})
    with pytest.raises(ValidationError, match="change_note"):
        IdeaRevisionDraft.model_validate({**REVISION_GOOD, "change_note": ""})


# ---------------------------------------------------------------- render round-trip


def test_render_idea_round_trips_through_parse():
    text = render_idea(REVISION_GOOD["prose"], REVISION_GOOD["musts"], REVISION_GOOD["nevers"])
    p = parse_idea(text)
    assert p.prose.strip() == REVISION_GOOD["prose"]
    assert p.musts == REVISION_GOOD["musts"] and p.nevers == REVISION_GOOD["nevers"]


def test_render_idea_without_nevers_omits_the_heading():
    text = render_idea("some prose about a thing that counts words on pages", ["count words"], [])
    assert "## Never" not in text and "## Must" in text


# ---------------------------------------------------------------- the stage


def test_upfront_questions_carry_the_idea():
    caller = MockCaller({InterviewQuestionsDraft: QUESTIONS_GOOD})
    qs, meta = interview.questions(idea_text=IDEA, caller=caller, cfg=CFG)
    assert [q.id for q in qs.questions] == ["Q1", "Q2", "Q3"]
    assert "counting words" in caller.calls[0]["user"]


def test_kill_loop_questions_carry_the_objections():
    panel = ReactionReport(run_id="r", parent="s", kill=True,
                           kill_reasons=["mean desirability 2.3 is below 2.5"],
                           means={"desirability": 2.3, "clarity": 4.0, "feasibility": 3.5},
                           cast=CAST_GOOD["personas"],
                           reactions=[
                               {"persona": p, "scores": {"desirability": 2, "clarity": 4, "feasibility": 3},
                                "objections": [f"{p} objection one", f"{p} objection two"],
                                "one_change": "change something"}
                               for p in SEATS])
    caller = MockCaller({InterviewQuestionsDraft: QUESTIONS_GOOD})
    interview.questions(idea_text=IDEA, caller=caller, cfg=CFG, panel=panel)
    user = caller.calls[0]["user"]
    assert "skeptic objection one" in user and "below 2.5" in user


def test_revise_folds_answers_and_renders():
    caller = MockCaller({IdeaRevisionDraft: REVISION_GOOD})
    rev, meta = interview.revise(
        idea_text=IDEA, caller=caller, cfg=CFG,
        qa=[("Who uses this?", "parents of toddlers"), ("Why better?", "parent voice")])
    assert rev.change_note
    assert "parents of toddlers" in caller.calls[0]["user"]


# ---------------------------------------------------------------- the develop loop


def _events(monkeypatch, tmp_path, outcomes, answers, approvals):
    """Wire fake run_one, canned interview answers and diff approvals into develop()."""
    from pipeline import runner as R

    import shutil
    shutil.copyfile("pipeline.toml", tmp_path / "pipeline.toml")
    idea = tmp_path / "idea.md"
    idea.write_text(IDEA)
    runs = iter(outcomes)
    ran = []

    class Out:
        def __init__(self, status):
            self.status = status
            self.run_dir = tmp_path / "runs" / f"r{len(ran)}"
            self.run_dir.mkdir(parents=True, exist_ok=True)
            if status == "killed":
                from pipeline.contracts import SEATS as _SEATS
                from tests.test_audience_casting import CAST_GOOD as _CG
                (self.run_dir / "03-panel.json").write_text(json.dumps({
                    "schema_version": "1", "stage": "panel", "run_id": "r", "parent": "s",
                    "kill": True, "kill_reasons": ["mean desirability 2.0 is below 2.5"],
                    "means": {"desirability": 2.0, "clarity": 4.0, "feasibility": 3.0},
                    "cast": _CG["personas"],
                    "reactions": [
                        {"persona": p, "scores": {"desirability": 2, "clarity": 4, "feasibility": 3},
                         "objections": ["obj a", "obj b"], "one_change": "x"}
                        for p in _SEATS]}))

    def fake_run_one(**kw):
        ran.append(kw)
        return Out(next(runs))

    monkeypatch.setattr(R, "run_one", fake_run_one)
    answer_iter = iter(answers)
    approval_iter = iter(approvals)

    def ask(prompt):
        return next(approval_iter) if "pprove" in prompt or "[y/N]" in prompt else next(answer_iter)

    caller = MockCaller({InterviewQuestionsDraft: QUESTIONS_GOOD, IdeaRevisionDraft: REVISION_GOOD})
    return R, idea, ran, ask, caller


def test_develop_interviews_upfront_then_runs(monkeypatch, tmp_path):
    R, idea, ran, ask, caller = _events(monkeypatch, tmp_path, outcomes=["success"],
                                        answers=["a1", "a2", "a3"], approvals=["y"])
    out = R.develop(root=tmp_path, graph="crew", idea_id=str(idea), yes=True, mock=False,
                    ask=ask, caller=caller)
    assert out.status == "success" and len(ran) == 1
    assert "parent-guided" in idea.read_text()  # the approved revision was written
    assert list(tmp_path.glob("idea.interview-*.md"))  # transcript saved beside the idea


def test_develop_kill_reinterviews_at_most_twice(monkeypatch, tmp_path):
    R, idea, ran, ask, caller = _events(
        monkeypatch, tmp_path, outcomes=["killed", "killed", "killed"],
        answers=["a"] * 9, approvals=["y", "y", "y"])
    out = R.develop(root=tmp_path, graph="crew", idea_id=str(idea), yes=True, mock=False,
                    ask=ask, caller=caller)
    assert out.status == "killed" and len(ran) == 3  # initial + 2 loops; third kill is final


def test_develop_declined_revision_stops_without_running(monkeypatch, tmp_path):
    R, idea, ran, ask, caller = _events(monkeypatch, tmp_path, outcomes=[],
                                        answers=["a1", "a2", "a3"], approvals=["n"])
    out = R.develop(root=tmp_path, graph="crew", idea_id=str(idea), yes=True, mock=False,
                    ask=ask, caller=caller)
    assert out is None and ran == [] and "counting words" in idea.read_text()  # untouched
