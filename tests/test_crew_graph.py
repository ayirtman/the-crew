"""The whole diagram executes: 14 stations, three program verifiers, one bounded repair,
a publish interrupt, a live health check. All against fakes; zero tokens."""
import json
import subprocess
from pathlib import Path

from pipeline import graph as G
from pipeline.config import load_config
from pipeline.contracts import (AudiencePackDraft, BriefDraft, BuildResult, CastingDraft, DomainPackDraft,
                                CommandResult, DesignSpecDraft, EvidencePackDraft,
                                PersonaReactionDraft, PlanDraft, RunManifest, SplitBuildResult,
                                TechSpecDraft, Usage, UXFlowsDraft)
from pipeline.contracts import TestReport as Report
from pipeline.llm import MockCaller
from pipeline.stages import CallMeta
from tests.test_crew_contracts import TECHSPEC_GOOD, UX_GOOD
from tests.test_audience_casting import AUDIENCE_GOOD, CAST_GOOD
from tests.test_domain import DOMAIN_GOOD
from tests.test_evidence import GOOD as EVIDENCE_GOOD

FIX = Path(__file__).parent / "fixtures"
TPL = FIX / "template_min"

PLAN_CREW = {
    "files": [
        {"path": "app/page.tsx", "purpose": "form with url input and result line"},
        {"path": "app/api/count/route.ts", "purpose": "POST handler that counts words"},
        {"path": "lib/count.ts", "purpose": "pure word counting"},
        {"path": "tests/api/count.test.ts", "purpose": "api tests"},
        {"path": "tests/ui/page.test.tsx", "purpose": "page tests"},
    ],
    "acceptance_criteria": [
        {"id": "AC1", "behavior_index": 0, "statement": "valid url returns count",
         "test_file": "tests/api/count.test.ts", "test_name": "returns word count for a valid url"},
        {"id": "AC2", "behavior_index": 1, "statement": "invalid url returns error",
         "test_file": "tests/api/count.test.ts", "test_name": "returns error for an invalid url"},
        {"id": "AC3", "behavior_index": 2, "statement": "button disabled in flight",
         "test_file": "tests/ui/page.test.tsx", "test_name": "disables the button while a request is in flight"},
    ],
    "build_steps": ["write lib", "write route", "write page and tests"],
}

PANEL_GOOD = {"scores": {"desirability": 4, "clarity": 4, "feasibility": 4},
              "objections": ["narrow use case", "no persistence"], "one_change": "add nothing"}

DESIGN_CREW = {"screens": [{
    "name": "main",
    "layout_description": "A card centered on the page: a url input, one btn-primary, a result-line under it",
    "components_used": ["card", "result-line", "btn-primary"],
    "maps_behaviors": [0, 1, 2],
    "covers_screen_ids": ["main"],
}]}

BACKEND_FILES = ["app/api/count/route.ts", "lib/count.ts", "tests/api/count.test.ts"]
FRONTEND_FILES = ["app/page.tsx", "tests/ui/page.test.tsx"]

CLEAN_AUDIT = json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}})
VERCEL_OUT = "Deploying...\nhttps://crew-app-xyz.vercel.app\n"


def _caller():
    return MockCaller({
        BriefDraft: json.loads((FIX / "brief_good.json").read_text()),
        EvidencePackDraft: EVIDENCE_GOOD,
        AudiencePackDraft: AUDIENCE_GOOD,
        DomainPackDraft: DOMAIN_GOOD,
        CastingDraft: CAST_GOOD,
        PersonaReactionDraft: PANEL_GOOD,
        PlanDraft: PLAN_CREW,
        TechSpecDraft: TECHSPEC_GOOD,
        UXFlowsDraft: UX_GOOD,
        DesignSpecDraft: DESIGN_CREW,
    })


class FakeSub:
    """One fake for audit and deploy runners: records calls, returns canned output."""

    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append({"argv": argv, "cwd": kw.get("cwd")})
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def _deps(tmp_path, *, file_content=None, verify_fails_first=False, repair_writes=None):
    """Crew deps: fake split build, sequenced fake verify, fake repair, fake audit/deploy/probe."""
    content = file_content or {}

    def fake_split(*, app_dir, run_dir, brief, plan, design, parent_sha, cfg, artifact_prefix="x", popen=None, audience=None, domain=None):
        for f in BACKEND_FILES + FRONTEND_FILES:
            p = Path(app_dir) / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content.get(f, "export {}"))

        def part(files):
            return BuildResult(run_id=brief.run_id, parent=parent_sha, app_dir=str(app_dir),
                               builder="fake", model="fake", files_written=files, subtype="success",
                               is_error=False, num_turns=1, duration_ms=1, total_cost_usd_reported=0.0,
                               billed_usd=0.0, usage=Usage(), permission_denials=0, result_text="",
                               exit_code=0)
        r = SplitBuildResult(run_id=brief.run_id, parent=parent_sha, app_dir=str(app_dir),
                             parts=[part(BACKEND_FILES), part(FRONTEND_FILES)],
                             roles=["backend", "frontend"],
                             files_written=sorted(BACKEND_FILES + FRONTEND_FILES), overlap=[])
        return r, CallMeta(model="fake", usage=Usage(), cost_reported=0.0, wall_ms=1)

    verify_calls = {"n": 0}

    def fake_verify(*, app_dir, run_dir, brief, plan, build_sha, cfg, runner=None):
        verify_calls["n"] += 1
        ok = not (verify_fails_first and verify_calls["n"] == 1)
        cmd = lambda n: CommandResult(name=n, argv=[n], exit_code=0 if ok else 1, duration_ms=1,
                                      passed=ok, stdout_tail="", stderr_tail="", timed_out=False)
        return Report(run_id=brief.run_id, parent=build_sha,
                          commands=[cmd("vitest"), cmd("eslint"), cmd("next_build"), cmd("tsc")],
                          tests=[], tests_passed=3 if ok else 0, tests_total=3, eslint_errors=0,
                          eslint_warnings=0, min_tests_required=3, criteria_coverage=[])

    def fake_repair(*, app_dir, run_dir, report, build_result, parent_sha, cfg, **kw):
        for f, c in (repair_writes or {}).items():
            (Path(app_dir) / f).write_text(c)
        r = BuildResult(run_id=report.run_id, parent=parent_sha, app_dir=str(app_dir), builder="fake",
                        model="fake", files_written=sorted(repair_writes or {"lib/count.ts": ""}),
                        subtype="success", is_error=False, num_turns=1, duration_ms=1,
                        total_cost_usd_reported=0.0, billed_usd=0.0, usage=Usage(),
                        permission_denials=0, result_text="", exit_code=0, stage="repair")
        return r, CallMeta(model="fake", usage=Usage(), cost_reported=0.0, wall_ms=1)

    audit = FakeSub(CLEAN_AUDIT)
    deploy = FakeSub(VERCEL_OUT)
    probes = []

    def probe(url):
        probes.append(url)
        return 200, 42, 1234

    deps = G.Deps(cfg=load_config("pipeline.toml"), caller=_caller(), build=fake_split,
                  build_split=fake_split,
                  verify=fake_verify, repair=fake_repair, template_dir=TPL,
                  apps_dir=tmp_path / "apps", runs_dir=tmp_path / "runs",
                  fetch=lambda url: 200, audit=audit, deploy=deploy, probe=probe)
    return deps, deploy, probes


def _idea(tmp_path):
    p = tmp_path / "01.md"
    p.write_text("A page where I paste a URL and get its word count.")
    return p


def _manifest(tmp_path, run_id):
    return RunManifest.model_validate_json((tmp_path / "runs" / run_id / "00-manifest.json").read_text())


def test_crew_happy_path_pauses_at_publish_then_ships_and_probes(tmp_path):
    deps, deploy, probes = _deps(tmp_path)
    h = G.start(deps=deps, variant="crew", idea_path=_idea(tmp_path), idea_id="01", run_id="c1", yes=True)
    assert h.next == ("ship",)  # --yes skips the build pause, never the publish pause
    run_dir = tmp_path / "runs" / "c1"
    for name in ("01-brief", "02-evidence", "03-audience", "04-domain", "05-panel", "06-plan",
                 "07-techspec", "08-ux", "09-design", "10-build", "11-review", "12-verify",
                 "13-security"):
        assert (run_dir / f"{name}.json").exists(), name
    out = h.resume()
    assert out.status == "success"
    assert (run_dir / "18-ship.json").exists() and (run_dir / "19-analytics.json").exists()
    assert not list(run_dir.glob("*-repair.json"))  # all green: repair never ran
    ship = json.loads((run_dir / "18-ship.json").read_text())
    assert ship["url"] == "https://crew-app-xyz.vercel.app"
    assert deploy.calls and "--prod" in deploy.calls[0]["argv"]
    assert probes == ["https://crew-app-xyz.vercel.app"]
    m = _manifest(tmp_path, "c1")
    assert [s.stage for s in m.stages] == ["intake", "evidence", "audience", "domain", "panel",
                                           "plan", "architect", "ux", "ui", "build_split",
                                           "review", "verify", "security", "ship", "analytics"]


def test_crew_verify_failure_repairs_once_then_reverifies_all_three(tmp_path):
    deps, deploy, probes = _deps(tmp_path, verify_fails_first=True)
    h = G.start(deps=deps, variant="crew", idea_path=_idea(tmp_path), idea_id="01", run_id="c2", yes=True)
    out = h.resume()
    run_dir = tmp_path / "runs" / "c2"
    assert out.status == "success"
    for name in ("14-repair", "15-review", "16-verify", "17-security", "18-ship"):
        assert (run_dir / f"{name}.json").exists(), name


def test_crew_security_failure_blocks_ship_when_repair_does_not_fix(tmp_path):
    bad = 'const k = eval("2+2")'
    deps, deploy, probes = _deps(tmp_path, file_content={"lib/count.ts": bad},
                                 repair_writes={"lib/count.ts": bad})
    out = G.start(deps=deps, variant="crew", idea_path=_idea(tmp_path), idea_id="01",
                  run_id="c3", yes=True)
    assert out.outcome is not None and out.outcome.status == "verify_failed"
    run_dir = tmp_path / "runs" / "c3"
    assert (run_dir / "14-repair.json").exists() and (run_dir / "17-security.json").exists()
    assert not list(run_dir.glob("*-ship.json"))
    assert not deploy.calls


def test_crew_security_failure_fixed_by_repair_ships(tmp_path):
    deps, deploy, probes = _deps(tmp_path, file_content={"lib/count.ts": 'eval("x")'},
                                 repair_writes={"lib/count.ts": "export const ok = 1"})
    h = G.start(deps=deps, variant="crew", idea_path=_idea(tmp_path), idea_id="01", run_id="c4", yes=True)
    out = h.resume()
    assert out.status == "success" and deploy.calls


def test_crew_decline_publish_is_verified_unshipped(tmp_path):
    deps, deploy, probes = _deps(tmp_path)
    h = G.start(deps=deps, variant="crew", idea_path=_idea(tmp_path), idea_id="01", run_id="c5", yes=True)
    assert h.next == ("ship",)
    out = h.decline_ship()
    assert out.status == "verified_unshipped"
    m = _manifest(tmp_path, "c5")
    assert m.status == "verified_unshipped" and m.finished_at is not None
    assert not deploy.calls and not list((tmp_path / "runs" / "c5").glob("*-ship.json"))


def test_crew_panel_kill_is_still_terminal(tmp_path):
    deps, deploy, probes = _deps(tmp_path)
    deps.caller.responses[PersonaReactionDraft] = {
        "scores": {"desirability": 0, "clarity": 2, "feasibility": 1},
        "objections": ["nobody wants this", "cannot be built"], "one_change": "different idea"}
    h = G.start(deps=deps, variant="crew", idea_path=_idea(tmp_path), idea_id="01", run_id="c6", yes=True)
    assert h.outcome is not None and h.outcome.status == "killed"
    assert not list((tmp_path / "runs" / "c6").glob("*-plan.json"))


def test_missing_planned_file_flows_to_repair_not_instant_death(tmp_path, monkeypatch):
    # 2026-09-03: two full builds died at the build gate over one missing planned test file.
    # In the crew, review owns that check and repair fixes it; build must not double-gate.
    deps, deploy, probes = _deps(tmp_path, repair_writes={"tests/ui/page.test.tsx": "test"})
    real_build = deps.build_split

    def build_missing_one(**kw):
        r, meta = real_build(**kw)
        (Path(r.app_dir) / "tests/ui/page.test.tsx").unlink()
        part_front = r.parts[1].model_copy(update={"files_written": ["app/page.tsx"]})
        r = r.model_copy(update={"parts": [r.parts[0], part_front],
                                 "files_written": sorted(set(r.files_written) - {"tests/ui/page.test.tsx"})})
        return r, meta

    deps = G.Deps(**{**deps.__dict__, "build_split": build_missing_one})
    h = G.start(deps=deps, variant="crew", idea_path=_idea(tmp_path), idea_id="01", run_id="c7", yes=True)
    out = h.resume()
    run_dir = tmp_path / "runs" / "c7"
    assert (run_dir / "11-review.json").exists()  # review caught it instead of the build gate
    assert (run_dir / "14-repair.json").exists()
    assert out.status == "success"


def test_split_task_prompt_lists_each_roles_planned_files():
    from pipeline.contracts import Brief, Plan
    from pipeline.stages import build_split
    b = Brief(run_id="r", idea_id="01", parent="s",
              **json.loads((FIX / "brief_good.json").read_text()))
    p = Plan(run_id="r", parent="s", constraints=[], **PLAN_CREW)
    backend = build_split._task("backend", b, p, None)
    frontend = build_split._task("frontend", b, p, None)
    assert "tests/api/count.test.ts" in backend.split("YOU MUST WRITE")[1]
    assert "tests/ui/page.test.tsx" in frontend.split("YOU MUST WRITE")[1]


def test_research_variant_runs_without_yes(tmp_path):
    # develop runs the research phase without --yes; a variant with no build node must not
    # try to pause before one (ValueError: Interrupt node `build` not found, 2026-09-03)
    deps, deploy, probes = _deps(tmp_path)
    out = G.run(deps=deps, variant="research", idea_path=_idea(tmp_path), idea_id="01",
                run_id="rs1", yes=False)
    assert out.status == "success"
    run_dir = tmp_path / "runs" / "rs1"
    for name in ("01-brief", "02-evidence", "03-audience", "04-domain"):
        assert (run_dir / f"{name}.json").exists(), name
