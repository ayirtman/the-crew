import json
import subprocess
from pathlib import Path

import pytest

from pipeline.budget import BudgetExceeded
from pipeline.config import load_config
from pipeline.contracts import Brief, Plan
from pipeline.stages import build

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _brief():
    d = json.loads((FIX / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def _plan():
    d = json.loads((FIX / "plan_good.json").read_text())
    return Plan(run_id="r1", parent="sha256:brief", constraints=["c"], **d)


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
            p.write_text("// written")
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def _app(tmp_path):
    app = tmp_path / "app"
    (app / "app").mkdir(parents=True)
    (app / "app" / "page.tsx").write_text("old")
    (app / "package.json").write_text("{}")
    return app


def _produce(tmp_path, runner, plan=None):
    return build.produce(app_dir=_app(tmp_path), run_dir=tmp_path / "run", brief=_brief(), plan=plan,
                         parent_sha="sha256:brief", cfg=CFG, runner=runner)


def test_argv_confines_tools_and_never_bypasses_permissions(tmp_path):
    r = FakeRunner(write=("app/page.tsx",))
    _produce(tmp_path, r)
    c = r.calls[0]
    argv = c["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert "--permission-mode" in argv and argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    assert "bypassPermissions" not in argv and "--dangerously-skip-permissions" not in argv
    assert "--safe-mode" in argv and "--bare" not in argv
    assert "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == "40"
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Write" in allowed and "Bash(npx vitest run" in allowed and "Bash(npx tsc" in allowed
    assert "ANTHROPIC_API_KEY" not in c["env"] and "CLAUDECODE" not in c["env"]
    assert c["cwd"].name == "app"
    assert c["timeout"] == 900 and c["kw"].get("start_new_session") is True


def test_result_reports_files_written_by_tree_diff(tmp_path):
    r = FakeRunner(write=("app/page.tsx", "tests/a.test.ts"))
    res, meta = _produce(tmp_path, r)
    assert res.files_written == ["app/page.tsx", "tests/a.test.ts"]
    assert res.builder == "claude_code" and res.billed_usd == 0.0 and res.parent == "sha256:brief"
    assert res.usage.cache_read_input_tokens > 0 and meta.wall_ms >= 0
    assert (tmp_path / "run" / "03-build.raw.json").exists()


def test_v1_prompt_embeds_plan_and_v0_prompt_does_not(tmp_path):
    r0 = FakeRunner(write=("app/page.tsx",))
    build.produce(app_dir=_app(tmp_path / "a"), run_dir=tmp_path / "ra", brief=_brief(), plan=None,
                  parent_sha="s", cfg=CFG, runner=r0)
    r1 = FakeRunner(write=("app/page.tsx",))
    build.produce(app_dir=_app(tmp_path / "b"), run_dir=tmp_path / "rb", brief=_brief(), plan=_plan(),
                  parent_sha="s", cfg=CFG, runner=r1)
    assert "acceptance_criteria" not in r0.calls[0]["argv"][2]
    assert "returns word count for a valid url" in r1.calls[0]["argv"][2]


def test_timeout_raises_budget_exceeded(tmp_path):
    with pytest.raises(BudgetExceeded, match="seconds"):
        _produce(tmp_path, FakeRunner(timeout=True))


def test_nonzero_exit_without_json_raises_builder_error(tmp_path):
    with pytest.raises(build.BuilderError):
        _produce(tmp_path, FakeRunner(stdout="boom", returncode=2))


def test_max_turns_exit_still_yields_a_build_result_with_error_flag(tmp_path):
    raw = json.loads((FIX / "claude_result_success.json").read_text())
    raw.update(is_error=True, subtype="error_max_turns", num_turns=13)
    res, meta = _produce(tmp_path, FakeRunner(stdout=json.dumps(raw), returncode=1, write=("app/page.tsx",)))
    assert res.is_error is True and res.subtype == "error_max_turns" and res.exit_code == 1
    assert res.files_written == ["app/page.tsx"]


def test_cost_over_stage_cap_raises_budget_exceeded(tmp_path):
    raw = json.loads((FIX / "claude_result_success.json").read_text())
    raw["total_cost_usd"] = 9.99
    with pytest.raises(BudgetExceeded, match="cost"):
        _produce(tmp_path, FakeRunner(stdout=json.dumps(raw), write=("app/page.tsx",)))


def test_build_detaches_stdin(tmp_path):
    r = FakeRunner(write=("app/page.tsx",))
    _produce(tmp_path, r)
    assert r.calls[0]["kw"].get("stdin") == subprocess.DEVNULL
