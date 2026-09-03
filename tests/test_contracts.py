import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline.contracts import (BriefDraft, BuildResult, CommandResult, CriterionCoverage, PlanDraft,
                                RunManifest, StageFailure, StageRecord)
from pipeline.contracts import TestCaseResult as CaseResult
from pipeline.contracts import TestReport as Report

FIX = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIX / name).read_text())


def test_good_brief_draft_validates():
    b = BriefDraft.model_validate(load("brief_good.json"))
    assert b.api.path == "/api/count"
    assert len(b.must_have_behaviors) == 3


def test_brief_rejects_vague_word():
    d = load("brief_good.json")
    d["problem"] = "A seamless way to count words."
    with pytest.raises(ValidationError, match="vague"):
        BriefDraft.model_validate(d)


def test_brief_rejects_too_few_behaviors():
    d = load("brief_good.json")
    d["must_have_behaviors"] = d["must_have_behaviors"][:2]
    with pytest.raises(ValidationError, match="3 and 8"):
        BriefDraft.model_validate(d)


def test_brief_rejects_api_path_outside_api():
    d = load("brief_good.json")
    d["api"]["path"] = "/count"
    with pytest.raises(ValidationError, match="/api/"):
        BriefDraft.model_validate(d)


def test_brief_rejects_duplicate_behaviors():
    d = load("brief_good.json")
    d["must_have_behaviors"][1] = d["must_have_behaviors"][0]
    with pytest.raises(ValidationError, match="duplicate"):
        BriefDraft.model_validate(d)


def test_good_plan_draft_validates():
    p = PlanDraft.model_validate(load("plan_good.json"))
    assert [c.id for c in p.acceptance_criteria] == ["AC1", "AC2", "AC3"]


def test_plan_rejects_duplicate_test_name():
    d = load("plan_good.json")
    d["acceptance_criteria"][1]["test_name"] = d["acceptance_criteria"][0]["test_name"]
    with pytest.raises(ValidationError, match="duplicate"):
        PlanDraft.model_validate(d)


def test_plan_rejects_test_file_not_in_files():
    d = load("plan_good.json")
    d["acceptance_criteria"][0]["test_file"] = "tests/other.test.ts"
    with pytest.raises(ValidationError, match="not in files"):
        PlanDraft.model_validate(d)


def test_plan_rejects_file_outside_allowed_dirs():
    d = load("plan_good.json")
    d["files"][0]["path"] = "package.json"
    with pytest.raises(ValidationError, match="app/, lib/ or tests/"):
        PlanDraft.model_validate(d)


def test_draft_schemas_carry_no_length_constraints():
    for model in (BriefDraft, PlanDraft):
        text = json.dumps(model.model_json_schema())
        for key in ("minItems", "maxItems", "minLength", "maxLength", "pattern"):
            assert key not in text, f"{model.__name__} schema has {key}"


# ---------------------------------------------------------------- run artifacts


def _cmd(name, passed=True, timed_out=False):
    return CommandResult(name=name, argv=["npx", name], exit_code=0 if passed else 1,
                         duration_ms=10, passed=passed, stdout_tail="", stderr_tail="",
                         timed_out=timed_out)


def _report(**kw):
    base = dict(
        run_id="r1", parent="sha256:abc",
        commands=[_cmd("tsc"), _cmd("eslint"), _cmd("vitest"), _cmd("next_build")],
        tests=[CaseResult(file="tests/a.test.ts", full_name="a", title="a", status="passed", duration_ms=1)],
        tests_passed=1, tests_total=1, eslint_errors=0, eslint_warnings=0,
        min_tests_required=1, criteria_coverage=[],
    )
    base.update(kw)
    return Report(**base)


def test_build_result_from_real_claude_json():
    raw = load("claude_result_success.json")
    r = BuildResult.from_claude_json(raw, run_id="r1", parent="sha256:abc", app_dir="apps/r1",
                                     model="haiku", billed=False, files_written=["app/page.tsx"],
                                     exit_code=0)
    assert r.subtype == "success" and r.is_error is False and r.num_turns == 1
    assert r.total_cost_usd_reported > 0 and r.billed_usd == 0.0
    assert r.usage.cache_read_input_tokens > 0
    assert r.permission_denials == 0


def test_test_report_passes_when_everything_green():
    assert _report().verify_pass is True


def test_test_report_fails_when_a_command_fails():
    r = _report(commands=[_cmd("tsc", passed=False), _cmd("eslint"), _cmd("vitest"), _cmd("next_build")])
    assert r.verify_pass is False


def test_test_report_fails_when_too_few_tests():
    assert _report(min_tests_required=3).verify_pass is False


def test_test_report_fails_when_a_criterion_is_missing():
    cov = [CriterionCoverage(criterion_id="AC1", test_name="a", found=True, status="passed"),
           CriterionCoverage(criterion_id="AC2", test_name="b", found=False, status=None)]
    assert _report(criteria_coverage=cov).verify_pass is False


def test_test_report_requires_exactly_four_commands():
    with pytest.raises(ValidationError, match="four"):
        _report(commands=[_cmd("tsc")])


def test_stage_failure_kinds_are_closed():
    StageFailure(run_id="r1", stage="intake", kind="evaluator_rejected", reasons=["x"])
    with pytest.raises(ValidationError):
        StageFailure(run_id="r1", stage="intake", kind="oops", reasons=[])


def test_manifest_totals_sum_stage_records():
    rec = lambda s, c, ms: StageRecord(stage=s, artifact_path=f"{s}.json", artifact_sha256="x",
                                       model="haiku", input_tokens=10, output_tokens=5,
                                       cache_read_tokens=0, cache_write_tokens=0, cost_usd=c,
                                       billed_usd=0.0, wall_ms=ms, evaluator_passed=True,
                                       evaluator_reasons=[], upstream_rejections=0)
    m = RunManifest(run_id="r1", graph="v1", idea_id="01", started_at="t0", finished_at="t1",
                    status="success", failed_stage=None,
                    stages=[rec("intake", 0.01, 100), rec("build", 0.5, 900)],
                    config_snapshot={}, pipeline_git_sha="abc", template_version="1",
                    claude_code_version="2.1.252")
    assert m.totals.cost_usd == pytest.approx(0.51)
    assert m.totals.wall_ms == 1000
    assert m.totals.input_tokens == 20


def test_brief_requirements_field_validates():
    b = BriefDraft.model_validate(load("brief_good.json"))
    assert b.requirements[0].kind == "never" and b.requirements[1].covered_by_behaviors == [1]


def test_brief_allows_up_to_eight_behaviors():
    d = load("brief_good.json")
    d["must_have_behaviors"] = [f"Return thing number {i}" for i in range(8)]
    assert len(BriefDraft.model_validate(d).must_have_behaviors) == 8
    d["must_have_behaviors"].append("Return thing number 8")
    with pytest.raises(ValidationError, match="3 and 8"):
        BriefDraft.model_validate(d)


def test_plan_ui_tests_must_be_tsx():
    # measured 2026-09-03: plan named tests/ui/page.test.ts, the builder correctly wrote .tsx
    # (JSX needs it), and the exact-path gate failed the run. The contract now refuses the trap.
    import json
    from pathlib import Path

    import pytest
    from pydantic import ValidationError

    from pipeline.contracts import PlanDraft
    d = json.loads((Path(__file__).parent / "fixtures" / "plan_good.json").read_text())
    d["files"].append({"path": "tests/ui/page.test.ts", "purpose": "ui test"})
    with pytest.raises(ValidationError, match="tsx"):
        PlanDraft.model_validate(d)
    d["files"][-1]["path"] = "tests/ui/page.test.tsx"
    PlanDraft.model_validate(d)
