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
