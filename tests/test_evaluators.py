import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import evaluators
from pipeline.contracts import Brief, BuildResult, Plan, PlanDraft, Usage

FIX = Path(__file__).parent / "fixtures"


def brief(**over):
    d = json.loads((FIX / "brief_good.json").read_text())
    d.update(over)
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def plan(b: Brief, **over):
    d = json.loads((FIX / "plan_good.json").read_text())
    d.update(over)
    return Plan(run_id="r1", parent="sha256:brief", constraints=[], **d)


def test_good_brief_has_no_reasons():
    assert evaluators.evaluate_brief(brief()) == []


def test_brief_behavior_must_start_with_a_verb_like_word():
    b = brief(must_have_behaviors=["The count for a valid URL", "Show an error", "Disable the button"])
    reasons = evaluators.evaluate_brief(b)
    assert any("verb" in r for r in reasons)


def test_brief_api_input_fields_required_for_post():
    b = brief(api={"path": "/api/count", "method": "POST", "input_fields": [], "output_fields": ["n"]})
    assert any("input_fields" in r for r in evaluators.evaluate_brief(b))


def test_good_plan_covers_every_behavior_once():
    b = brief()
    assert evaluators.evaluate_plan(plan(b), b) == []


def test_plan_must_have_one_criterion_per_behavior():
    b = brief()
    d = json.loads((FIX / "plan_good.json").read_text())
    d["acceptance_criteria"] = d["acceptance_criteria"][:2]
    reasons = evaluators.evaluate_plan(plan(b, **d), b)
    assert any("behavior 2" in r for r in reasons)


def test_plan_behavior_index_out_of_range_is_rejected():
    b = brief()
    d = json.loads((FIX / "plan_good.json").read_text())
    d["acceptance_criteria"][0]["behavior_index"] = 7
    assert any("behavior_index" in r for r in evaluators.evaluate_plan(plan(b, **d), b))


def test_plan_contract_rejects_test_file_missing_from_files():
    d = json.loads((FIX / "plan_good.json").read_text())
    d["files"] = [f for f in d["files"] if not f["path"].startswith("tests/")]
    d["files"].append({"path": "lib/other.ts", "purpose": "x"})
    with pytest.raises(ValidationError, match="not in files"):
        PlanDraft.model_validate({k: d[k] for k in ("files", "acceptance_criteria", "build_steps")})


def _build(files_written, is_error=False, subtype="success"):
    return BuildResult(run_id="r1", parent="sha256:plan", app_dir="apps/r1", builder="fake", model="haiku",
                       files_written=files_written, subtype=subtype, is_error=is_error, num_turns=3,
                       duration_ms=10, total_cost_usd_reported=0.0, billed_usd=0.0, usage=Usage(),
                       permission_denials=0, result_text="", exit_code=0)


def test_build_rejects_edits_to_locked_files():
    r = _build(["app/page.tsx", "package.json"])
    assert any("package.json" in x for x in evaluators.evaluate_build(r, None))


def test_build_rejects_when_nothing_written():
    assert any("no files" in x for x in evaluators.evaluate_build(_build([]), None))


def test_build_rejects_error_result():
    r = _build(["app/page.tsx"], is_error=True, subtype="error_max_turns")
    assert any("error_max_turns" in x for x in evaluators.evaluate_build(r, None))


def test_build_with_plan_requires_planned_test_files_to_exist():
    b = brief()
    p = plan(b)
    r = _build(["app/page.tsx", "app/api/count/route.ts", "lib/count.ts"])
    assert any("tests/count.test.ts" in x for x in evaluators.evaluate_build(r, p))


def test_build_with_plan_passes_when_test_files_written():
    b = brief()
    p = plan(b)
    r = _build(["app/page.tsx", "app/api/count/route.ts", "lib/count.ts", "tests/count.test.ts"])
    assert evaluators.evaluate_build(r, p) == []
