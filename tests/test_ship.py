import json
from pathlib import Path

import pytest

from pipeline import ship
from pipeline.contracts import RunManifest, ShipRecord

FIX = Path(__file__).parent / "fixtures"


def _run_dir(tmp_path, status="success", verify_pass=True, with_repair=False):
    d = tmp_path / "runs" / "v1-01-x"
    d.mkdir(parents=True)
    m = {"schema_version": "1", "run_id": "v1-01-x", "graph": "v1", "idea_id": "01", "started_at": "t",
         "finished_at": "t", "status": status, "failed_stage": None, "stages": [], "variant_stages": [],
         "config_snapshot": {}, "pipeline_git_sha": "x", "template_version": "3", "claude_code_version": None}
    (d / "00-manifest.json").write_text(json.dumps(m))
    app = tmp_path / "apps" / "v1-01-x"
    app.mkdir(parents=True)
    build = {"schema_version": "1", "stage": "build", "run_id": "v1-01-x", "parent": "s",
             "app_dir": str(app), "builder": "claude_code", "model": "haiku", "files_written": ["app/page.tsx"],
             "subtype": "success", "is_error": False, "num_turns": 5, "duration_ms": 1,
             "total_cost_usd_reported": 0.1, "billed_usd": 0.0,
             "usage": {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                       "cache_read_input_tokens": 0},
             "permission_denials": 0, "session_id": None, "result_text": "", "exit_code": 0}
    (d / "03-build.json").write_text(json.dumps(build))
    verify = {"verify_pass": verify_pass, "tests_passed": 3, "tests_total": 3}
    (d / "04-verify.json").write_text(json.dumps(verify))
    if with_repair:
        rep = dict(build, stage="repair", app_dir=str(app))
        (d / "05-repair.json").write_text(json.dumps(rep))
        (d / "06-verify.json").write_text(json.dumps({"verify_pass": True, "tests_passed": 3, "tests_total": 3}))
    return tmp_path


VERCEL_OUT = "Retrieving project...\nProducing optimized build...\nhttps://toddler-app-abc123.vercel.app\n"


class FakeRunner:
    def __init__(self, stdout=VERCEL_OUT, returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr
        self.calls = []

    def __call__(self, argv, *, cwd, timeout, **kw):
        self.calls.append({"argv": argv, "cwd": Path(cwd)})
        import subprocess
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)


def test_refuses_unless_run_succeeded(tmp_path, capsys):
    root = _run_dir(tmp_path, status="verify_failed")
    assert ship.ship(root=root, run_id="v1-01-x", runner=FakeRunner()) != 0
    assert not list((root / "runs" / "v1-01-x").glob("*-ship.json"))


def test_refuses_when_final_verify_failed(tmp_path):
    root = _run_dir(tmp_path, status="success", verify_pass=False)
    assert ship.ship(root=root, run_id="v1-01-x", runner=FakeRunner()) != 0


def test_happy_path_deploys_and_writes_ship_record(tmp_path):
    root = _run_dir(tmp_path)
    r = FakeRunner()
    assert ship.ship(root=root, run_id="v1-01-x", runner=r) == 0
    argv = r.calls[0]["argv"]
    assert argv[:2] == ["npx", "vercel"] and "--prod" in argv and "--yes" in argv
    assert r.calls[0]["cwd"] == root / "apps" / "v1-01-x"
    rec = ShipRecord.model_validate_json(next((root / "runs" / "v1-01-x").glob("*-ship.json")).read_text())
    assert rec.url == "https://toddler-app-abc123.vercel.app"
    m = RunManifest.model_validate_json((root / "runs" / "v1-01-x" / "00-manifest.json").read_text())
    assert m.stages and m.stages[-1].stage == "ship" and m.status == "success"


def test_repaired_run_deploys_the_repaired_app(tmp_path):
    root = _run_dir(tmp_path, with_repair=True)
    r = FakeRunner()
    assert ship.ship(root=root, run_id="v1-01-x", runner=r) == 0


def test_login_error_is_surfaced_not_automated(tmp_path, capsys):
    root = _run_dir(tmp_path)
    r = FakeRunner(stdout="", returncode=1, stderr="Error: No existing credentials found. Please run `vercel login`")
    assert ship.ship(root=root, run_id="v1-01-x", runner=r) != 0
    assert "vercel login" in capsys.readouterr().out
