import json
import logging
from pathlib import Path

from pipeline import graph as G
from pipeline.config import load_config
from pipeline.contracts import (BriefDraft, BuildResult, CommandResult, PlanDraft, RunManifest, StageFailure,
                                Usage)
from pipeline.contracts import TestReport as Report
from pipeline.llm import CallResult, MockCaller
from pipeline.stages import CallMeta

FIX = Path(__file__).parent / "fixtures"
TPL = FIX / "template_min"


def _cfg():
    return load_config("pipeline.toml")


def _caller(brief_over=None):
    b = json.loads((FIX / "brief_good.json").read_text())
    if brief_over:
        b.update(brief_over)
    return MockCaller({BriefDraft: b, PlanDraft: json.loads((FIX / "plan_good.json").read_text())})


def _deps(tmp_path, caller=None, cfg=None, verify_ok=True, build_files=None):
    """Fake builder drops files in the app dir; fake verify returns a canned report."""
    files = build_files if build_files is not None else [
        "app/page.tsx", "app/api/count/route.ts", "lib/count.ts", "tests/count.test.ts"]

    def fake_build(*, app_dir, run_dir, brief, plan, parent_sha, cfg, runner=None, artifact_prefix="03-build"):
        for f in files:
            p = Path(app_dir) / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        r = BuildResult(run_id=brief.run_id, parent=parent_sha, app_dir=str(app_dir), builder="fake",
                        model="fake", files_written=files, subtype="success", is_error=False, num_turns=1,
                        duration_ms=5, total_cost_usd_reported=0.0, billed_usd=0.0, usage=Usage(),
                        permission_denials=0, result_text="", exit_code=0)
        return r, CallMeta(model="fake", usage=Usage(), cost_reported=0.0, wall_ms=5)

    def fake_verify(*, app_dir, run_dir, brief, plan, build_sha, cfg, runner=None):
        cmd = lambda n: CommandResult(name=n, argv=[n], exit_code=0 if verify_ok else 1, duration_ms=1,
                                      passed=verify_ok, stdout_tail="", stderr_tail="", timed_out=False)
        return Report(run_id=brief.run_id, parent=build_sha,
                      commands=[cmd("vitest"), cmd("eslint"), cmd("next_build"), cmd("tsc")],
                      tests=[], tests_passed=3 if verify_ok else 0, tests_total=3,
                      eslint_errors=0, eslint_warnings=0, min_tests_required=3, criteria_coverage=[])

    return G.Deps(cfg=cfg or _cfg(), caller=caller or _caller(), build=fake_build, verify=fake_verify,
                  template_dir=TPL, apps_dir=tmp_path / "apps", runs_dir=tmp_path / "runs")


def _idea(tmp_path):
    p = tmp_path / "01.md"
    p.write_text("A page where I paste a URL and get its word count.")
    return p


def _manifest(tmp_path, run_id):
    return RunManifest.model_validate_json((tmp_path / "runs" / run_id / "00-manifest.json").read_text())


def test_v1_reaches_finish_with_ordered_parent_chain(tmp_path):
    out = G.run(deps=_deps(tmp_path), variant="v1", idea_path=_idea(tmp_path), idea_id="01", run_id="r1", yes=True)
    run_dir = tmp_path / "runs" / "r1"
    names = sorted(p.name for p in run_dir.glob("0?-*.json"))
    assert names == ["00-manifest.json", "01-brief.json", "02-plan.json", "03-build.json", "04-verify.json"]
    m = _manifest(tmp_path, "r1")
    assert m.status == "success" and [s.stage for s in m.stages] == ["intake", "plan", "build", "verify"]
    plan = json.loads((run_dir / "02-plan.json").read_text())
    assert plan["parent"] == m.stages[0].artifact_sha256
    assert out.status == "success"


def test_v0_skips_plan_and_numbers_by_execution_order(tmp_path):
    G.run(deps=_deps(tmp_path), variant="v0", idea_path=_idea(tmp_path), idea_id="01", run_id="r0", yes=True)
    run_dir = tmp_path / "runs" / "r0"
    names = sorted(p.name for p in run_dir.glob("0?-*.json"))
    assert names == ["00-manifest.json", "01-brief.json", "02-build.json", "03-verify.json"]
    build = json.loads((run_dir / "02-build.json").read_text())
    assert build["parent"] == _manifest(tmp_path, "r0").stages[0].artifact_sha256


def test_bad_brief_fails_at_intake_and_writes_nothing_downstream(tmp_path):
    deps = _deps(tmp_path, caller=_caller({"problem": "A seamless experience"}))
    out = G.run(deps=deps, variant="v1", idea_path=_idea(tmp_path), idea_id="01", run_id="r2", yes=True)
    run_dir = tmp_path / "runs" / "r2"
    assert out.status == "failed"
    names = sorted(p.name for p in run_dir.glob("*.json"))
    assert names == ["00-manifest.json", "01-failure.json"]
    f = StageFailure.model_validate_json((run_dir / "01-failure.json").read_text())
    assert f.kind == "schema_invalid" and f.stage == "intake"
    assert not (tmp_path / "apps" / "r2").exists()


def test_evaluator_rejection_is_a_failure_artifact(tmp_path):
    caller = _caller({"must_have_behaviors": ["The count", "Show an error", "Disable the button"]})
    out = G.run(deps=_deps(tmp_path, caller=caller), variant="v0", idea_path=_idea(tmp_path), idea_id="01",
                run_id="r3", yes=True)
    f = StageFailure.model_validate_json((tmp_path / "runs" / "r3" / "01-failure.json").read_text())
    assert out.status == "failed" and f.kind == "evaluator_rejected" and any("verb" in r for r in f.reasons)
    assert not (tmp_path / "runs" / "r3" / "01-brief.json").exists()  # never accepted, so never an artifact
    assert (tmp_path / "runs" / "r3" / "01-brief.rejected.json").exists()  # but the last draft is kept
    assert f.rejected_artifact_path.endswith("01-brief.rejected.json")


def test_run_time_cap_of_zero_stops_before_build(tmp_path):
    cfg = _cfg()
    cfg = cfg.model_copy(update={"run": cfg.run.model_copy(update={"max_seconds": 0.0})})
    out = G.run(deps=_deps(tmp_path, cfg=cfg), variant="v0", idea_path=_idea(tmp_path), idea_id="01",
                run_id="r4", yes=True)
    run_dir = tmp_path / "runs" / "r4"
    assert out.status == "failed"
    f = StageFailure.model_validate_json(next(run_dir.glob("*-failure.json")).read_text())
    assert f.kind == "budget_exceeded"
    assert not (run_dir / "03-build.json").exists()


def test_verify_failure_is_data_not_a_crash(tmp_path):
    out = G.run(deps=_deps(tmp_path, verify_ok=False), variant="v0", idea_path=_idea(tmp_path), idea_id="01",
                run_id="r5", yes=True)
    assert out.status == "verify_failed"
    assert list((tmp_path / "runs" / "r5").glob("*-verify.json"))


def test_without_yes_the_graph_pauses_before_build(tmp_path):
    paused = G.start(deps=_deps(tmp_path), variant="v1", idea_path=_idea(tmp_path), idea_id="01", run_id="r6", yes=False)
    assert paused.next == ("build",)
    assert (tmp_path / "runs" / "r6" / "02-plan.json").exists()
    assert not (tmp_path / "runs" / "r6" / "03-build.json").exists()
    out = paused.resume()
    assert out.status == "success" and (tmp_path / "runs" / "r6" / "03-build.json").exists()


def test_abort_at_pause_writes_aborted_manifest(tmp_path):
    paused = G.start(deps=_deps(tmp_path), variant="v0", idea_path=_idea(tmp_path), idea_id="01", run_id="r7", yes=False)
    out = paused.abort()
    assert out.status == "aborted" and _manifest(tmp_path, "r7").status == "aborted"
    f = StageFailure.model_validate_json(next((tmp_path / "runs" / "r7").glob("*-failure.json")).read_text())
    assert f.kind == "aborted_by_user"


def test_manifest_records_upstream_rejection_when_input_is_tampered(tmp_path):
    # Build re-validates the artifact it receives; a tampered brief counts as an upstream rejection.
    paused = G.start(deps=_deps(tmp_path), variant="v0", idea_path=_idea(tmp_path), idea_id="01", run_id="r8", yes=False)
    brief = tmp_path / "runs" / "r8" / "01-brief.json"
    brief.write_text(brief.read_text().replace("URL Word Counter", "Tampered"))
    out = paused.resume()
    m = _manifest(tmp_path, "r8")
    assert out.status == "failed"
    assert m.stages[-1].stage == "build" and m.stages[-1].upstream_rejections == 1


def test_manifest_is_rewritten_after_every_stage_not_only_at_the_end(tmp_path):
    G.start(deps=_deps(tmp_path), variant="v1", idea_path=_idea(tmp_path), idea_id="01", run_id="r9", yes=False)
    m = _manifest(tmp_path, "r9")
    assert m.status == "running" and [s.stage for s in m.stages] == ["intake", "plan"]
    assert m.finished_at is None


def test_subscription_stages_report_cost_but_bill_nothing(tmp_path):
    class CostingCaller(MockCaller):
        def call(self, **kw):
            r = super().call(**kw)
            return CallResult(parsed=r.parsed, usage=Usage(input_tokens=10, output_tokens=5), cost_reported=0.03,
                              num_turns=2, duration_ms=10, raw={})

    caller = CostingCaller({BriefDraft: json.loads((FIX / "brief_good.json").read_text()),
                            PlanDraft: json.loads((FIX / "plan_good.json").read_text())})
    out = G.run(deps=_deps(tmp_path, caller=caller), variant="v1", idea_path=_idea(tmp_path), idea_id="01",
                run_id="r10", yes=True)
    intake_rec = out.manifest.stages[0]
    assert intake_rec.cost_usd == 0.03 and intake_rec.billed_usd == 0.0
    assert out.manifest.totals.billed_usd == 0.0 and out.manifest.totals.cost_usd > 0


def test_each_stage_logs_start_and_outcome(tmp_path, caplog):
    with caplog.at_level(logging.INFO, logger="pipeline"):
        G.run(deps=_deps(tmp_path), variant="v1", idea_path=_idea(tmp_path), idea_id="01", run_id="r11", yes=True)
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("[intake] start") for m in msgs)
    assert any(m.startswith("[plan] ok") for m in msgs)
    assert any(m.startswith("[verify] ok") for m in msgs)
    assert any(m.startswith("[run] success") for m in msgs)


# ---------------------------------------------------------------- repair path


def _deps_with_repair(tmp_path, first_verify_ok, repair_fixes):
    """First verify fails or passes; the fake repair 'fixes' the app; verify2 reflects repair_fixes."""
    deps = _deps(tmp_path, verify_ok=first_verify_ok)
    calls = {"n": 0}
    real_fake_verify = deps.verify

    def verify_seq(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_fake_verify(**kw)
        return _deps(tmp_path / "x2", verify_ok=repair_fixes).verify(**kw)

    def fake_repair(*, app_dir, run_dir, report, build_result, parent_sha, cfg, runner=None,
                    artifact_prefix="05-repair"):
        from pipeline.contracts import BuildResult, Usage
        from pipeline.stages import CallMeta
        r = BuildResult(run_id=report.run_id, parent=parent_sha, app_dir=str(app_dir), builder="fake",
                        model="fake", files_written=["app/page.tsx"], subtype="success", is_error=False,
                        num_turns=2, duration_ms=3, total_cost_usd_reported=0.0, billed_usd=0.0,
                        usage=Usage(), permission_denials=0, result_text="", exit_code=0, stage="repair")
        return r, CallMeta(model="fake", usage=Usage(), cost_reported=0.0, wall_ms=3)

    return G.Deps(cfg=deps.cfg, caller=deps.caller, build=deps.build, verify=verify_seq,
                  template_dir=deps.template_dir, apps_dir=deps.apps_dir, runs_dir=deps.runs_dir,
                  repair=fake_repair)


def test_v1r_repair_fires_on_failure_and_second_verify_decides_success(tmp_path):
    out = G.run(deps=_deps_with_repair(tmp_path, first_verify_ok=False, repair_fixes=True), variant="v1r",
                idea_path=_idea(tmp_path), idea_id="01", run_id="rr1", yes=True)
    run_dir = tmp_path / "runs" / "rr1"
    assert out.status == "success"
    assert (run_dir / "05-repair.json").exists() and (run_dir / "06-verify.json").exists()
    m = _manifest(tmp_path, "rr1")
    assert [s.stage for s in m.stages] == ["intake", "plan", "build", "verify", "repair", "verify2"]
    rep = json.loads((run_dir / "05-repair.json").read_text())
    assert rep["stage"] == "repair"


def test_v1r_unrepaired_failure_stays_verify_failed(tmp_path):
    out = G.run(deps=_deps_with_repair(tmp_path, first_verify_ok=False, repair_fixes=False), variant="v1r",
                idea_path=_idea(tmp_path), idea_id="01", run_id="rr2", yes=True)
    assert out.status == "verify_failed"
    assert (tmp_path / "runs" / "rr2" / "06-verify.json").exists()


def test_v2e_writes_evidence_between_brief_and_plan(tmp_path):
    from pipeline.contracts import EvidencePackDraft
    from tests.test_evidence import GOOD
    caller = _caller()
    caller.responses[EvidencePackDraft] = GOOD
    deps = _deps_with_repair(tmp_path, first_verify_ok=True, repair_fixes=True)
    deps = G.Deps(cfg=deps.cfg, caller=caller, build=deps.build, verify=deps.verify, repair=deps.repair,
                  template_dir=deps.template_dir, apps_dir=deps.apps_dir, runs_dir=deps.runs_dir,
                  fetch=lambda url: 200)
    out = G.run(deps=deps, variant="v2e", idea_path=_idea(tmp_path), idea_id="01", run_id="e1", yes=True)
    run_dir = tmp_path / "runs" / "e1"
    assert out.status == "success"
    ev = json.loads((run_dir / "02-evidence.json").read_text())
    m = _manifest(tmp_path, "e1")
    assert ev["parent"] == m.stages[0].artifact_sha256
    plan = json.loads((run_dir / "03-plan.json").read_text())
    assert plan["parent"] == m.stages[0].artifact_sha256  # plan still parents the brief
