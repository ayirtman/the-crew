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


def test_build_that_ran_out_of_turns_but_wrote_files_goes_to_verify():
    r = _build(["app/page.tsx"], is_error=True, subtype="error_max_turns")
    assert evaluators.evaluate_build(r, None) == []


def test_build_that_ran_out_of_turns_and_wrote_nothing_is_rejected():
    r = _build([], is_error=True, subtype="error_max_turns")
    assert any("no files" in x for x in evaluators.evaluate_build(r, None))


def test_build_rejects_other_error_results():
    r = _build(["app/page.tsx"], is_error=True, subtype="error_during_execution")
    assert any("error_during_execution" in x for x in evaluators.evaluate_build(r, None))


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


def test_brief_accepts_any_verb_not_just_a_whitelist():
    b = brief(must_have_behaviors=["Advance to the next word after each answer", "Show an error", "Disable the button"])
    assert evaluators.evaluate_brief(b) == []


def test_brief_rejects_behaviors_that_start_with_an_article_or_subject():
    for bad in ("The count for a valid URL", "A list of words", "It shows the word", "Users can click", "There is a timer"):
        b = brief(must_have_behaviors=[bad, "Show an error", "Disable the button"])
        assert any("verb" in r for r in evaluators.evaluate_brief(b)), bad


# ---------------------------------------------------------------- requirement coverage

from pipeline.idea import ParsedIdea  # noqa: E402


def _idea(musts=(), nevers=()):
    return ParsedIdea(prose="p", musts=list(musts), nevers=list(nevers))


def _req(text, kind, beh=(), ng=()):
    return {"text": text, "kind": kind, "covered_by_behaviors": list(beh), "covered_by_non_goals": list(ng)}


def test_brief_covering_all_musts_and_nevers_passes():
    b = brief(requirements=[_req("Each session has a time limit.", "must", beh=[0]),
                            _req("no rewards", "never", ng=[0])])
    idea = _idea(musts=["each session has a time limit"], nevers=["No rewards"])
    assert evaluators.evaluate_brief(b, idea) == []


def test_dropped_must_is_rejected_with_its_text():
    b = brief(requirements=[])
    idea = _idea(musts=["each session has a time limit"])
    reasons = evaluators.evaluate_brief(b, idea)
    assert any("each session has a time limit" in r and "must" in r.lower() for r in reasons)


def test_must_without_behavior_coverage_is_rejected():
    b = brief(requirements=[_req("each session has a time limit", "must")])
    reasons = evaluators.evaluate_brief(b, _idea(musts=["each session has a time limit"]))
    assert any("not covered by any behavior" in r for r in reasons)


def test_never_must_map_to_non_goals_not_behaviors():
    b = brief(requirements=[_req("no rewards", "never", beh=[0])])
    reasons = evaluators.evaluate_brief(b, _idea(nevers=["no rewards"]))
    assert any("non_goal" in r for r in reasons)


def test_requirement_indexes_out_of_range_rejected():
    b = brief(requirements=[_req("x behavior thing", "prose", beh=[9])])
    reasons = evaluators.evaluate_brief(b, _idea())
    assert any("out of range" in r for r in reasons)


def test_prose_requirement_needs_some_coverage():
    b = brief(requirements=[_req("tracked accuracy", "prose")])
    reasons = evaluators.evaluate_brief(b, _idea())
    assert any("tracked accuracy" in r for r in reasons)


def test_evaluate_brief_without_idea_defaults_to_empty_contract():
    assert evaluators.evaluate_brief(brief()) == []
