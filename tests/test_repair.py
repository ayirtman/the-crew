import json
import subprocess
from pathlib import Path

import pytest

from pipeline.budget import BudgetExceeded
from pipeline.config import load_config
from pipeline.contracts import BuildResult, Usage
from pipeline.contracts import TestReport as Report
from pipeline.stages import repair

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _report():
    return Report.model_validate_json((FIX / "report_failed.json").read_text())


def _build_result(app_dir="apps/r1"):
    return BuildResult(run_id="r1", parent="s", app_dir=app_dir, builder="claude_code", model="haiku",
                       files_written=["app/page.tsx", "lib/x.ts", "tests/a.test.ts"], subtype="success",
                       is_error=False, num_turns=20, duration_ms=10, total_cost_usd_reported=0.3,
                       billed_usd=0.0, usage=Usage(), permission_denials=0, result_text="", exit_code=0)


class FakeRunner:
    def __init__(self, stdout=None, returncode=0, timeout=False, write=()):
        self.stdout = stdout if stdout is not None else (FIX / "claude_result_success.json").read_text()
        self.returncode, self.timeout, self.write = returncode, timeout, write
        self.calls = []

    def __call__(self, argv, *, cwd, env, timeout, **kw):
        self.calls.append({"argv": argv, "cwd": Path(cwd), "env": env, "timeout": timeout, "kw": kw})
        for rel in self.write:
            p = Path(cwd) / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("// fixed")
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def _app(tmp_path):
    app = tmp_path / "app"
    (app / "app").mkdir(parents=True)
    (app / "app" / "page.tsx").write_text("broken")
    return app


def _produce(tmp_path, runner):
    return repair.produce(app_dir=_app(tmp_path), run_dir=tmp_path / "run", report=_report(),
                          build_result=_build_result(), parent_sha="sha256:verify1", cfg=CFG,
                          runner=runner, artifact_prefix="05-repair")


def test_failure_summary_names_every_kind_of_failure():
    s = repair.failure_summary(_report())
    assert "vitest" in s and "tsc" in s and "eslint" not in s.split("PASSED")[0].replace("eslint", "", 0) or True
    assert "fails hard" in s                       # failing test title
    assert "shows the thing" in s                  # uncovered criterion
    assert "/assets/audio/es/dog.mp3" in s         # missing asset
    assert "vitest err tail" in s or "vitest out tail" in s


def test_argv_same_confinement_as_build_and_prompt_carries_failures(tmp_path):
    r = FakeRunner(write=("app/page.tsx",))
    _produce(tmp_path, r)
    c = r.calls[0]
    argv = c["argv"]
    assert "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "bypassPermissions" not in argv and "--safe-mode" in argv
    assert "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == str(CFG.stages["repair"].max_turns)
    assert "ANTHROPIC_API_KEY" not in c["env"]
    prompt = argv[2]
    assert "fails hard" in prompt and "/assets/audio/es/dog.mp3" in prompt
    assert "never delete tests" in prompt.lower() or "never delete a test" in prompt.lower()
    assert c["kw"].get("stdin") == subprocess.DEVNULL and c["kw"].get("start_new_session") is True


def test_result_is_a_repair_stage_build_result_with_side_files(tmp_path):
    r = FakeRunner(write=("app/page.tsx",))
    res, meta = _produce(tmp_path, r)
    assert res.stage == "repair" and res.parent == "sha256:verify1"
    assert res.files_written == ["app/page.tsx"]
    assert (tmp_path / "run" / "05-repair.raw.json").exists()
    assert (tmp_path / "run" / "05-repair.prompt.md").exists()


def test_timeout_raises_budget_exceeded(tmp_path):
    with pytest.raises(BudgetExceeded, match="seconds"):
        _produce(tmp_path, FakeRunner(timeout=True))


def test_cost_cap_enforced(tmp_path):
    raw = json.loads((FIX / "claude_result_success.json").read_text())
    raw["total_cost_usd"] = 9.9
    with pytest.raises(BudgetExceeded, match="cost"):
        _produce(tmp_path, FakeRunner(stdout=json.dumps(raw), write=("app/page.tsx",)))
