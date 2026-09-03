import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline import evaluators
from pipeline.config import load_config
from pipeline.contracts import Brief, EvidencePack, EvidencePackDraft
from pipeline.llm import MockCaller
from pipeline.stages import evidence

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")

GOOD = {
    "claims": [
        {"statement": "Toddlers learn nouns fastest from paired image and audio", "source_url": "https://a.example.org/x", "source_title": "A study", "retrieved": True},
        {"statement": "Parent voices improve toddler attention over stranger voices", "source_url": "https://b.example.com/y", "source_title": "B paper", "retrieved": True},
        {"statement": "Session limits under five minutes fit toddler attention spans", "source_url": "https://a.example.org/z", "source_title": "A guide", "retrieved": True},
    ],
    "competitors": [{"name": "Studycat", "url": "https://studycat.com", "note": "subscription toddler app"}],
    "search_queries_used": ["toddler language app research"],
}


def _brief():
    d = json.loads((FIX / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def _pack(over=None, searches=2):
    d = json.loads(json.dumps(GOOD))
    if over:
        d.update(over)
    return EvidencePack(run_id="r1", parent="sha256:brief", web_search_requests=searches, **d)


def test_contract_rejects_non_http_source():
    d = json.loads(json.dumps(GOOD))
    d["claims"][0]["source_url"] = "ftp://nope"
    with pytest.raises(ValidationError, match="http"):
        EvidencePackDraft.model_validate(d)


def test_contract_rejects_vague_claim():
    d = json.loads(json.dumps(GOOD))
    d["claims"][0]["statement"] = "A seamless learning experience"
    with pytest.raises(ValidationError, match="vague"):
        EvidencePackDraft.model_validate(d)


def _ok_fetch(url):
    return 200


def test_evaluator_passes_good_pack():
    assert evaluators.evaluate_evidence(_pack(), CFG.evidence, fetch=_ok_fetch) == []


def test_evaluator_rejects_zero_web_searches():
    reasons = evaluators.evaluate_evidence(_pack(searches=0), CFG.evidence, fetch=_ok_fetch)
    assert any("web search" in r.lower() for r in reasons)


def test_evaluator_rejects_too_few_claims_and_domains():
    d = json.loads(json.dumps(GOOD))
    d["claims"] = d["claims"][:2]
    for c in d["claims"]:
        c["source_url"] = "https://only.example.net/p"
    p = _pack({"claims": d["claims"]})
    reasons = evaluators.evaluate_evidence(p, CFG.evidence, fetch=_ok_fetch)
    assert any("claims" in r for r in reasons) and any("domain" in r for r in reasons)


def test_evaluator_rejects_dead_urls_but_accepts_bot_blocks():
    def fetch(url):
        if "a.example.org" in url:
            return 403      # alive, bot-blocked: acceptable
        return 404          # dead
    reasons = evaluators.evaluate_evidence(_pack(), CFG.evidence, fetch=fetch)
    assert any("b.example.com" in r for r in reasons)
    assert not any("a.example.org" in r for r in reasons)


def test_evaluator_treats_network_error_as_dead():
    def fetch(url):
        raise OSError("no dns")
    reasons = evaluators.evaluate_evidence(_pack(), CFG.evidence, fetch=fetch)
    assert reasons and all("unreachable" in r or "dead" in r for r in reasons if "http" in r)


def test_stage_wraps_pack_with_search_count_from_raw(tmp_path):
    class CountingCaller(MockCaller):
        def call(self, **kw):
            r = super().call(**kw)
            raw = {"modelUsage": {"claude-haiku-4-5-20251001": {"webSearchRequests": 3},
                                  "other": {"webSearchRequests": 1}}}
            return type(r)(parsed=r.parsed, usage=r.usage, cost_reported=0.01, num_turns=4,
                           duration_ms=5, raw=raw)

    caller = CountingCaller({EvidencePackDraft: GOOD})
    pack, meta = evidence.produce(brief=_brief(), parent_sha="sha256:brief", caller=caller, cfg=CFG,
                                  fetch=_ok_fetch)
    assert pack.web_search_requests == 4 and pack.parent == "sha256:brief"


def test_cli_caller_tools_flag_comes_from_stage_config():
    from pipeline.llm import ClaudeCliCaller
    from pipeline.contracts import BriefDraft
    c = ClaudeCliCaller()
    argv_intake = c.argv(system_file=Path("x"), user="u", schema=BriefDraft, stage=CFG.stages["intake"])
    argv_ev = c.argv(system_file=Path("x"), user="u", schema=EvidencePackDraft, stage=CFG.stages["evidence"])
    assert argv_intake[argv_intake.index("--tools") + 1] == ""
    assert argv_ev[argv_ev.index("--tools") + 1] == "WebSearch"
    assert "--allowedTools" in argv_ev and argv_ev[argv_ev.index("--allowedTools") + 1] == "WebSearch"
    assert "--allowedTools" not in argv_intake


def test_one_dead_source_among_enough_live_ones_passes():
    # 2026-09-03: a single rotted URL killed a whole run. Fabrication is the enemy, not link rot:
    # the gate is "enough claims with live sources", never "zero dead links".
    d = json.loads(json.dumps(GOOD))
    d["claims"].append({"statement": "Bilingual toddlers separate languages by speaker context",
                        "source_url": "https://c.example.net/rotted", "source_title": "C study",
                        "retrieved": True})
    p = _pack({"claims": d["claims"]})
    def fetch(url):
        return 404 if "c.example.net" in url else 200
    assert evaluators.evaluate_evidence(p, CFG.evidence, fetch=fetch) == []
