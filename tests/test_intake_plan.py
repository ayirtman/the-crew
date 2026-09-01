import json
from pathlib import Path

import pytest

from pipeline.budget import BudgetExceeded
from pipeline.config import load_config
from pipeline.contracts import Brief, BriefDraft, PlanDraft
from pipeline.llm import MockCaller
from pipeline.stages import intake, plan

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _caller():
    return MockCaller({
        BriefDraft: json.loads((FIX / "brief_good.json").read_text()),
        PlanDraft: json.loads((FIX / "plan_good.json").read_text()),
    })


def test_intake_wraps_draft_with_envelope_and_idea_hash():
    brief, meta = intake.produce(idea_text="a url word counter", idea_sha="sha256:idea", run_id="r1",
                                 idea_id="01", caller=_caller(), cfg=CFG)
    assert isinstance(brief, Brief)
    assert brief.parent == "sha256:idea" and brief.run_id == "r1" and brief.idea_id == "01"
    assert meta.model == CFG.stages["intake"].model


def test_intake_refuses_oversized_idea_before_calling():
    with pytest.raises(BudgetExceeded, match="input"):
        intake.produce(idea_text="x" * 50_000, idea_sha="s", run_id="r1", idea_id="01",
                       caller=_caller(), cfg=CFG)


def test_plan_wraps_draft_with_parent_and_fixed_constraints():
    brief, _ = intake.produce(idea_text="idea", idea_sha="sha256:idea", run_id="r1", idea_id="01",
                              caller=_caller(), cfg=CFG)
    p, meta = plan.produce(brief=brief, brief_sha="sha256:brief", caller=_caller(), cfg=CFG)
    assert p.parent == "sha256:brief" and p.run_id == "r1"
    assert any("package.json" in c for c in p.constraints)
    assert meta.wall_ms >= 0


def test_intake_retries_when_the_evaluator_rejects_the_draft():
    from pipeline.llm import CallResult, SchemaInvalid
    from pipeline.contracts import Usage
    good = json.loads((FIX / "brief_good.json").read_text())
    bad = dict(good, must_have_behaviors=["The count", "Show an error", "Disable the button"])

    class Seq:
        def __init__(self):
            self.n = 0
        def call(self, *, system_file, user, schema, stage):
            self.n += 1
            return CallResult(parsed=schema.model_validate(bad if self.n == 1 else good), usage=Usage(),
                              cost_reported=0.0, num_turns=2, duration_ms=1, raw={})

    s = Seq()
    brief, meta = intake.produce(idea_text="idea", idea_sha="s", run_id="r1", idea_id="01", caller=s, cfg=CFG)
    assert s.n == 2 and meta.attempts == 2 and brief.must_have_behaviors[0].startswith("Return")
