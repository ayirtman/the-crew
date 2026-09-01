import json
import subprocess
from pathlib import Path

from pipeline.config import load_config
from pipeline.contracts import Brief, Plan
from pipeline.stages import verify

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _brief():
    d = json.loads((FIX / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def _plan():
    d = json.loads((FIX / "plan_good.json").read_text())
    return Plan(run_id="r1", parent="sha256:brief", constraints=[], **d)


VITEST_JSON = {
    "numPassedTests": 2, "numFailedTests": 1, "numTotalTests": 3,
    "testResults": [{
        "name": "/abs/apps/r1/tests/count.test.ts",
        "assertionResults": [
            {"fullName": "count returns word count for a valid url", "title": "returns word count for a valid url", "status": "passed", "duration": 2.5},
            {"fullName": "count returns error for an invalid url", "title": "returns error for an invalid url", "status": "passed", "duration": 1.0},
            {"fullName": "ui disables the button while a request is in flight", "title": "disables the button while a request is in flight", "status": "failed", "duration": 3.0},
        ],
    }],
}
ESLINT_JSON = [{"filePath": "a.ts", "errorCount": 1, "warningCount": 2}, {"filePath": "b.ts", "errorCount": 0, "warningCount": 1}]


class FakeRunner:
    """Simulates the four commands. Writes the json output files the real tools would."""

    def __init__(self, fail=(), timeout=(), vitest=VITEST_JSON, eslint=ESLINT_JSON):
        self.fail, self.timeout, self.vitest, self.eslint = set(fail), set(timeout), vitest, eslint
        self.calls = []

    def __call__(self, argv, *, cwd, env, timeout, **kw):
        self.calls.append(argv)
        name = verify.command_name(argv)
        if name in self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        if name == "vitest":
            out = next(a for a in argv if a.startswith("--outputFile=")).split("=", 1)[1]
            Path(out).write_text(json.dumps(self.vitest))
        if name == "eslint":
            Path(argv[argv.index("-o") + 1]).write_text(json.dumps(self.eslint))
        rc = 1 if name in self.fail else 0
        return subprocess.CompletedProcess(argv, rc, "out", "err")


def _produce(tmp_path, app_dir=None, **kw):
    args = dict(app_dir=app_dir or tmp_path / "app", run_dir=tmp_path / "run", brief=_brief(), plan=None,
                build_sha="sha256:build", cfg=CFG, runner=FakeRunner())
    args.update(kw)
    return verify.produce(**args)


def test_runs_four_commands_in_oracle_order(tmp_path):
    r = FakeRunner()
    _produce(tmp_path, runner=r)
    assert [verify.command_name(a) for a in r.calls] == ["vitest", "eslint", "next_build", "tsc"]


def test_parses_vitest_and_eslint_counts(tmp_path):
    rep = _produce(tmp_path)
    assert rep.tests_total == 3 and rep.tests_passed == 2
    assert rep.eslint_errors == 1 and rep.eslint_warnings == 3
    assert rep.tests[0].file == "tests/count.test.ts"
    assert rep.parent == "sha256:build"


def test_vitest_command_fails_when_any_test_fails_even_with_exit_zero(tmp_path):
    rep = _produce(tmp_path)
    assert rep.commands[0].name == "vitest" and rep.commands[0].passed is False
    assert rep.verify_pass is False


def test_all_green_passes_without_plan(tmp_path):
    ok = json.loads(json.dumps(VITEST_JSON))
    ok.update(numFailedTests=0, numPassedTests=3)
    ok["testResults"][0]["assertionResults"][2]["status"] = "passed"
    rep = _produce(tmp_path, runner=FakeRunner(vitest=ok, eslint=[]))
    assert rep.verify_pass is True and rep.criteria_coverage == []


def test_criteria_coverage_matches_plan_test_names(tmp_path):
    rep = _produce(tmp_path, plan=_plan())
    cov = {c.criterion_id: c for c in rep.criteria_coverage}
    assert cov["AC1"].found and cov["AC1"].status == "passed"
    assert cov["AC3"].found and cov["AC3"].status == "failed"
    assert rep.verify_pass is False


def test_missing_criterion_is_reported_not_found(tmp_path):
    v = json.loads(json.dumps(VITEST_JSON))
    v["testResults"][0]["assertionResults"].pop(1)
    v["numTotalTests"] = 2
    rep = _produce(tmp_path, plan=_plan(), runner=FakeRunner(vitest=v))
    ac2 = next(c for c in rep.criteria_coverage if c.criterion_id == "AC2")
    assert ac2.found is False and ac2.status is None


def test_timeout_marks_command_timed_out_and_continues(tmp_path):
    r = FakeRunner(timeout=("next_build",))
    rep = _produce(tmp_path, runner=r)
    nb = next(c for c in rep.commands if c.name == "next_build")
    assert nb.timed_out is True and nb.passed is False
    assert len(r.calls) == 4


def test_min_tests_required_comes_from_brief(tmp_path):
    assert _produce(tmp_path).min_tests_required == 3


# ---------------------------------------------------------------- asset references


def _app_with_assets(tmp_path, refs, files):
    app = tmp_path / "app"
    (app / "app").mkdir(parents=True)
    (app / "lib").mkdir()
    (app / "app" / "page.tsx").write_text("const urls = [" + ", ".join(f'"{r}"' for r in refs) + "];")
    for f in files:
        p = app / "public" / f.lstrip("/")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    return app


def test_asset_refs_found_in_source_and_checked_against_public(tmp_path):
    app = _app_with_assets(tmp_path, ["/assets/images/dog.svg", "/assets/audio/en/dog.mp3"],
                           ["/assets/images/dog.svg"])
    rep = _produce(tmp_path, app_dir=app)
    assert rep.asset_refs_total == 2
    assert rep.asset_refs_missing == ["/assets/audio/en/dog.mp3"]
    assert rep.verify_pass is False


def test_all_asset_refs_resolving_passes(tmp_path):
    app = _app_with_assets(tmp_path, ["/assets/images/dog.svg"], ["/assets/images/dog.svg"])
    ok = json.loads(json.dumps(VITEST_JSON))
    ok.update(numFailedTests=0, numPassedTests=3)
    ok["testResults"][0]["assertionResults"][2]["status"] = "passed"
    rep = _produce(tmp_path, app_dir=app, runner=FakeRunner(vitest=ok, eslint=[]))
    assert rep.asset_refs_total == 1 and rep.asset_refs_missing == []
    assert rep.verify_pass is True


def test_no_asset_refs_is_fine(tmp_path):
    rep = _produce(tmp_path)
    assert rep.asset_refs_total == 0 and rep.asset_refs_missing == []


def test_duplicate_refs_counted_once_and_node_modules_ignored(tmp_path):
    app = _app_with_assets(tmp_path, ["/assets/x.svg", "/assets/x.svg"], [])
    nm = app / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "junk.js").write_text('"/assets/ghost.mp3"')
    rep = _produce(tmp_path, app_dir=app)
    assert rep.asset_refs_total == 1 and rep.asset_refs_missing == ["/assets/x.svg"]
