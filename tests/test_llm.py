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
    assert "--max-turns" in argv and argv[argv.index("--max-turns") + 1] == "4"
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


from pipeline.llm import call_with_retry  # noqa: E402


class FlakyCaller:
    """Rejects the first N calls with SchemaInvalid, then returns the good draft."""

    def __init__(self, bad_times, good):
        self.bad_times, self.good, self.calls = bad_times, good, []

    def call(self, *, system_file, user, schema, stage):
        self.calls.append(user)
        if len(self.calls) <= self.bad_times:
            raise SchemaInvalid(["problem: vague word 'simple'"])
        from pipeline.contracts import Usage
        from pipeline.llm import CallResult
        return CallResult(parsed=schema.model_validate(self.good), usage=Usage(input_tokens=7, output_tokens=3),
                          cost_reported=0.01, num_turns=2, duration_ms=10, raw={})


def test_retry_feeds_rejection_reasons_back_and_sums_usage():
    good = json.loads((FIX / "brief_good.json").read_text())
    c = FlakyCaller(1, good)
    res = call_with_retry(c, system_file=Path("x.md"), user="idea", schema=BriefDraft,
                          stage=CFG.stages["intake"], attempts=2)
    assert len(c.calls) == 2
    assert "vague word 'simple'" in c.calls[1] and c.calls[1].startswith("idea")
    assert res.parsed.title == "URL Word Counter"
    assert res.attempts == 2 and res.usage.input_tokens == 7


def test_retry_gives_up_after_attempts_and_raises_last_reasons():
    c = FlakyCaller(5, json.loads((FIX / "brief_good.json").read_text()))
    with pytest.raises(SchemaInvalid, match="simple"):
        call_with_retry(c, system_file=Path("x.md"), user="idea", schema=BriefDraft,
                        stage=CFG.stages["intake"], attempts=2)
    assert len(c.calls) == 2


def test_intake_and_plan_use_the_configured_attempts():
    assert CFG.stages["intake"].max_attempts == 2 and CFG.stages["plan"].max_attempts == 2


def test_retry_also_feeds_back_check_reasons():
    good = json.loads((FIX / "brief_good.json").read_text())
    c = FlakyCaller(0, good)
    seen = []

    def check(draft):
        seen.append(draft)
        return ["must_have_behaviors[0] does not start with a verb"] if len(seen) == 1 else []

    res = call_with_retry(c, system_file=Path("x.md"), user="idea", schema=BriefDraft,
                          stage=CFG.stages["intake"], attempts=2, check=check)
    assert len(c.calls) == 2 and "start with a verb" in c.calls[1]
    assert res.attempts == 2 and res.rejections == [["must_have_behaviors[0] does not start with a verb"]]


def test_retry_raises_schema_invalid_when_check_still_fails_on_last_attempt():
    good = json.loads((FIX / "brief_good.json").read_text())
    c = FlakyCaller(0, good)
    with pytest.raises(SchemaInvalid, match="verb"):
        call_with_retry(c, system_file=Path("x.md"), user="idea", schema=BriefDraft,
                        stage=CFG.stages["intake"], attempts=2, check=lambda d: ["no verb"])


def test_nonzero_exit_with_structured_output_is_salvaged():
    stdout = _stdout_with(json.loads((FIX / "brief_good.json").read_text()))
    r = FakeRunner(stdout, returncode=1)
    res = ClaudeCliCaller(runner=r).call(system_file=Path("x.md"), user="i", schema=BriefDraft,
                                         stage=CFG.stages["intake"])
    assert isinstance(res.parsed, BriefDraft)


def test_nonzero_exit_without_structured_output_is_still_an_error():
    raw = json.loads((FIX / "claude_result_success.json").read_text())
    raw["is_error"] = True
    with pytest.raises(CallerError, match="max_turns|exited|reported"):
        ClaudeCliCaller(runner=FakeRunner(json.dumps(raw), returncode=1)).call(
            system_file=Path("x.md"), user="i", schema=BriefDraft, stage=CFG.stages["intake"])
