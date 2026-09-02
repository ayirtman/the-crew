"""Crew stations 5 (Architect -> TechSpec) and 6 (UX Designer -> UXFlows), and the UI stage
gaining screen coverage against the UXFlows."""
import json
from pathlib import Path

from pipeline import evaluators
from pipeline.config import load_config
from pipeline.contracts import (Brief, DesignSpec, Plan, TechSpec, TechSpecDraft, UXFlows,
                                UXFlowsDraft)
from pipeline.llm import MockCaller
from pipeline.stages import architect, ux as ux_stage
from tests.test_crew_contracts import TECHSPEC_GOOD, UX_GOOD

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _brief():
    return Brief(run_id="r1", idea_id="01", parent="s", **json.loads((FIX / "brief_good.json").read_text()))


def _plan():
    return Plan(run_id="r1", parent="s", constraints=[], **json.loads((FIX / "plan_good.json").read_text()))


def _techspec(over=None):
    d = json.loads(json.dumps(TECHSPEC_GOOD))
    if over:
        d.update(over)
    return TechSpec(run_id="r1", parent="s", **d)


def _ux(over=None):
    d = json.loads(json.dumps(UX_GOOD))
    if over:
        d.update(over)
    return UXFlows(run_id="r1", parent="s", **d)


# ---------------------------------------------------------------- evaluate_techspec


def test_good_techspec_passes():
    assert evaluators.evaluate_techspec(_techspec(), _plan()) == []


def test_interface_file_not_in_plan_is_rejected():
    bad = json.loads(json.dumps(TECHSPEC_GOOD))
    bad["interfaces"][0]["backend_file"] = "app/api/other/route.ts"
    reasons = evaluators.evaluate_techspec(_techspec(bad), _plan())
    assert any("not in the plan" in r for r in reasons)


def test_interface_files_must_respect_the_split_scopes():
    bad = json.loads(json.dumps(TECHSPEC_GOOD))
    # frontend_file placed in the backend's scope
    bad["interfaces"][0]["frontend_file"] = "lib/count.ts"
    reasons = evaluators.evaluate_techspec(_techspec(bad), _plan())
    assert any("frontend" in r and "scope" in r for r in reasons)


def test_at_least_one_api_interface_required():
    bad = json.loads(json.dumps(TECHSPEC_GOOD))
    bad["interfaces"][0]["backend_file"] = "lib/count.ts"
    reasons = evaluators.evaluate_techspec(_techspec(bad), _plan())
    assert any("app/api/" in r for r in reasons)


# ---------------------------------------------------------------- evaluate_uxflows


def test_good_uxflows_passes():
    assert evaluators.evaluate_uxflows(_ux(), _brief()) == []


def test_uncovered_behavior_is_rejected():
    bad = json.loads(json.dumps(UX_GOOD))
    bad["flows"][0]["covers_behaviors"] = [0]
    reasons = evaluators.evaluate_uxflows(_ux(bad), _brief())
    assert any("behavior 1" in r for r in reasons) and any("behavior 2" in r for r in reasons)


def test_step_referencing_unknown_screen_is_rejected():
    bad = json.loads(json.dumps(UX_GOOD))
    bad["flows"][0]["steps"] = [{"screen_id": "ghost", "action": "tap"}]
    reasons = evaluators.evaluate_uxflows(_ux(bad), _brief())
    assert any("ghost" in r for r in reasons)


def test_orphan_screen_is_rejected():
    bad = json.loads(json.dumps(UX_GOOD))
    bad["screens"].append({"id": "settings", "name": "Settings", "purpose": "unused screen"})
    reasons = evaluators.evaluate_uxflows(_ux(bad), _brief())
    assert any("settings" in r and "no flow" in r for r in reasons)


def test_out_of_range_behavior_index_is_rejected():
    bad = json.loads(json.dumps(UX_GOOD))
    bad["flows"][0]["covers_behaviors"] = [0, 1, 2, 9]
    reasons = evaluators.evaluate_uxflows(_ux(bad), _brief())
    assert any("out of range" in r for r in reasons)


# ---------------------------------------------------------------- design gains screen coverage


DESIGN_GOOD = {
    "screens": [{
        "name": "main",
        "layout_description": "A card centered on the page with a url input, one btn-primary, a result-line",
        "components_used": ["card", "btn-primary", "result-line"],
        "maps_behaviors": [0, 1, 2],
        "covers_screen_ids": ["main"],
    }]
}
COMPONENTS = ["card", "btn-primary", "result-line"]


def test_design_covering_every_ux_screen_passes():
    d = DesignSpec(run_id="r1", parent="s", **DESIGN_GOOD)
    assert evaluators.evaluate_design(d, _brief(), COMPONENTS, ux=_ux()) == []


def test_design_missing_a_ux_screen_is_rejected():
    bad = json.loads(json.dumps(DESIGN_GOOD))
    bad["screens"][0]["covers_screen_ids"] = []
    d = DesignSpec(run_id="r1", parent="s", **bad)
    reasons = evaluators.evaluate_design(d, _brief(), COMPONENTS, ux=_ux())
    assert any("main" in r and "not covered" in r for r in reasons)


def test_design_claiming_unknown_ux_screen_is_rejected():
    bad = json.loads(json.dumps(DESIGN_GOOD))
    bad["screens"][0]["covers_screen_ids"] = ["main", "ghost"]
    d = DesignSpec(run_id="r1", parent="s", **bad)
    reasons = evaluators.evaluate_design(d, _brief(), COMPONENTS, ux=_ux())
    assert any("ghost" in r for r in reasons)


def test_design_without_ux_keeps_old_behavior():
    d = DesignSpec(run_id="r1", parent="s", **DESIGN_GOOD)
    assert evaluators.evaluate_design(d, _brief(), COMPONENTS) == []


# ---------------------------------------------------------------- the stages


def test_architect_stage_wraps_and_prompts_with_plan_and_scopes():
    caller = MockCaller({TechSpecDraft: TECHSPEC_GOOD})
    spec, meta = architect.produce(brief=_brief(), plan=_plan(), parent_sha="sha:plan",
                                   caller=caller, cfg=CFG)
    assert spec.parent == "sha:plan" and spec.stage == "architect"
    user = caller.calls[0]["user"]
    assert "app/api/count/route.ts" in user and "backend" in user.lower()


def test_ux_stage_wraps_and_prompts_with_behaviors():
    caller = MockCaller({UXFlowsDraft: UX_GOOD})
    flows, meta = ux_stage.produce(brief=_brief(), plan=_plan(), parent_sha="sha:techspec",
                                   caller=caller, cfg=CFG)
    assert flows.parent == "sha:techspec" and flows.stage == "ux"
    assert "must_have_behaviors" in caller.calls[0]["user"] or "behavior" in caller.calls[0]["user"]


def test_design_stage_accepts_ux_and_mentions_screens(tmp_path):
    from pipeline.stages import design
    from tests.test_design import GOOD
    good = json.loads(json.dumps(GOOD))
    good["screens"][0]["covers_screen_ids"] = ["main"]
    from pipeline.contracts import DesignSpecDraft
    caller = MockCaller({DesignSpecDraft: good})
    spec, meta = design.produce(brief=_brief(), evidence=None, parent_sha="sha:ux",
                                components=["card", "image-tile", "result-line", "btn-primary"],
                                caller=caller, cfg=CFG, ux=_ux())
    assert spec.parent == "sha:ux"
    assert "UX FLOWS" in caller.calls[0]["user"]
