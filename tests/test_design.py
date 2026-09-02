import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import evaluators
from pipeline.config import load_config
from pipeline.contracts import Brief, DesignSpec, DesignSpecDraft
from pipeline.llm import MockCaller
from pipeline.stages import design

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[1]
CFG = load_config("pipeline.toml")

GOOD = {
    "screens": [{
        "name": "main",
        "layout_description": "A card centered on the page: two image-tiles side by side, a result-line under them, one btn-primary labeled Next at the bottom",
        "components_used": ["card", "image-tile", "result-line", "btn-primary"],
        "maps_behaviors": [0, 1, 2],
    }]
}


def _brief():
    d = json.loads((FIX / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def _spec(over=None):
    d = json.loads(json.dumps(GOOD))
    if over:
        d.update(over)
    return DesignSpec(run_id="r1", parent="sha256:plan", **d)


def test_corpus_files_exist_and_parse():
    comps = design.load_components(ROOT / "templates" / "next-app")
    assert "btn-primary" in comps and "image-tile" in comps
    assert (ROOT / "templates" / "next-app" / "design" / "corpus.md").exists()
    assert (ROOT / "tests" / "fixtures" / "template_min" / "design" / "components.json").exists()


def test_contract_rejects_vague_layout():
    d = json.loads(json.dumps(GOOD))
    d["screens"][0]["layout_description"] = "A simple clean layout"
    with pytest.raises(ValidationError, match="vague"):
        DesignSpecDraft.model_validate(d)


def test_evaluator_rejects_unknown_component():
    spec = _spec()
    bad = json.loads(json.dumps(GOOD))
    bad["screens"][0]["components_used"] = ["card", "hero-banner"]
    spec = DesignSpec(run_id="r1", parent="s", **bad)
    reasons = evaluators.evaluate_design(spec, _brief(), ["card", "image-tile"])
    assert any("hero-banner" in r for r in reasons)


def test_evaluator_requires_every_behavior_mapped():
    reasons = evaluators.evaluate_design(_spec(), _brief(), GOOD["screens"][0]["components_used"])
    # brief has 3 behaviors, all mapped -> no behavior reasons
    assert reasons == []
    bad = json.loads(json.dumps(GOOD))
    bad["screens"][0]["maps_behaviors"] = [0]
    reasons = evaluators.evaluate_design(DesignSpec(run_id="r1", parent="s", **bad), _brief(),
                                         GOOD["screens"][0]["components_used"])
    assert any("behavior 1" in r for r in reasons) and any("behavior 2" in r for r in reasons)


def test_evaluator_rejects_out_of_range_behavior_index():
    bad = json.loads(json.dumps(GOOD))
    bad["screens"][0]["maps_behaviors"] = [0, 1, 2, 9]
    reasons = evaluators.evaluate_design(DesignSpec(run_id="r1", parent="s", **bad), _brief(),
                                         GOOD["screens"][0]["components_used"])
    assert any("out of range" in r for r in reasons)


def test_stage_wraps_spec_and_build_prompt_gains_design_section(tmp_path):
    caller = MockCaller({DesignSpecDraft: GOOD})
    spec, meta = design.produce(brief=_brief(), evidence=None, parent_sha="sha256:plan",
                                components=["card", "image-tile", "result-line", "btn-primary"],
                                caller=caller, cfg=CFG)
    assert spec.parent == "sha256:plan"
    assert "btn-primary" in caller.calls[0]["user"]

    from pipeline.stages import build
    p_with = build.task_prompt(_brief(), None, design=spec)
    p_without = build.task_prompt(_brief(), None)
    assert "DESIGN:" in p_with and "DESIGN:" not in p_without
