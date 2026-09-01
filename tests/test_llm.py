import json
import subprocess
from pathlib import Path

import pytest

from pipeline.budget import BudgetExceeded
from pipeline.config import load_config
from pipeline.contracts import BriefDraft
from pipeline.llm import CallerError, ClaudeCliCaller, MockCaller, SchemaInvalid

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _stdout_with(structured):
    raw = json.loads((FIX / "claude_result_success.json").read_text())
    raw["structured_output"] = structured
    raw["num_turns"] = 2
    return json.dumps(raw)


class FakeRunner:
    def __init__(self, stdout="", returncode=0, timeout=False):
        self.stdout, self.returncode, self.timeout = stdout, returncode, timeout
        self.calls = []

    def __call__(self, argv, *, cwd, env, timeout, **kw):
        self.calls.append({"argv": argv, "cwd": cwd, "env": env, "timeout": timeout, "kw": kw})
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, timeout)
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_argv_uses_json_schema_no_tools_and_subscription_safe_flags():
    r = FakeRunner(_stdout_with(json.loads((FIX / "brief_good.json").read_text())))
    caller = ClaudeCliCaller(runner=r)
    caller.call(system_file=Path("pipeline/prompts/intake_system.md"), user="idea", schema=BriefDraft,
                stage=CFG.stages["intake"])
    argv = r.calls[0]["argv"]
    assert argv[:2] == ["claude", "-p"]
    assert "--json-schema" in argv and "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    assert "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == "2"
    assert "--safe-mode" in argv and "--bare" not in argv
    assert "--append-system-prompt-file" in argv
    env = r.calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDECODE" not in env
    assert r.calls[0]["timeout"] == CFG.stages["intake"].max_seconds


def test_call_returns_parsed_draft_and_usage():
    r = FakeRunner(_stdout_with(json.loads((FIX / "brief_good.json").read_text())))
    res = ClaudeCliCaller(runner=r).call(system_file=Path("x.md"), user="idea", schema=BriefDraft,
                                         stage=CFG.stages["intake"])
    assert isinstance(res.parsed, BriefDraft)
    assert res.usage.cache_read_input_tokens > 0
    assert res.num_turns == 2 and res.cost_reported > 0


def test_call_raises_schema_invalid_with_reasons_when_output_breaks_contract():
    bad = json.loads((FIX / "brief_good.json").read_text())
    bad["problem"] = "A seamless experience"
    r = FakeRunner(_stdout_with(bad))
    with pytest.raises(SchemaInvalid) as ei:
        ClaudeCliCaller(runner=r).call(system_file=Path("x.md"), user="i", schema=BriefDraft,
                                       stage=CFG.stages["intake"])
    assert any("vague" in x for x in ei.value.reasons)


def test_call_raises_caller_error_on_nonzero_exit():
    r = FakeRunner("", returncode=1)
    with pytest.raises(CallerError):
        ClaudeCliCaller(runner=r).call(system_file=Path("x.md"), user="i", schema=BriefDraft,
                                       stage=CFG.stages["intake"])


def test_call_raises_budget_exceeded_on_timeout():
    r = FakeRunner(timeout=True)
    with pytest.raises(BudgetExceeded, match="seconds"):
        ClaudeCliCaller(runner=r).call(system_file=Path("x.md"), user="i", schema=BriefDraft,
                                       stage=CFG.stages["intake"])


def test_mock_caller_returns_fixture_for_schema():
    m = MockCaller({BriefDraft: json.loads((FIX / "brief_good.json").read_text())})
    res = m.call(system_file=Path("x.md"), user="i", schema=BriefDraft, stage=CFG.stages["intake"])
    assert res.parsed.title == "URL Word Counter" and res.cost_reported == 0.0


def test_cli_call_detaches_stdin_so_it_cannot_eat_the_pause_answer():
    r = FakeRunner(_stdout_with(json.loads((FIX / "brief_good.json").read_text())))
    ClaudeCliCaller(runner=r).call(system_file=Path("x.md"), user="i", schema=BriefDraft, stage=CFG.stages["intake"])
    assert r.calls[0]["kw"].get("stdin") == subprocess.DEVNULL
