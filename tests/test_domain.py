"""The third research station: what the subject matter itself demands. Born 2026-09-03 after
the machine shipped bare nouns in languages where the article is part of the word."""
import json

import pytest
from pydantic import ValidationError

from pipeline.config import load_config
from pipeline.contracts import Brief, DomainPack, DomainPackDraft
from pipeline.llm import MockCaller
from pipeline.stages import domain
from pipeline.variants import VARIANTS, expand

CFG = load_config("pipeline.toml")

DOMAIN_GOOD = {
    "non_negotiables": [
        {"finding": "In German, Spanish and French the article is part of the noun and must be learned with it",
         "implication": "words display and sound with their article: der Hund, la manzana, le chien",
         "source_url": "https://example.org/articles", "source_title": "Gendered articles in L2 acquisition"},
        {"finding": "Vocabulary retention in toddlers requires spaced repetition across sessions",
         "implication": "word selection weights recent failures higher across days",
         "source_url": "https://example.com/spacing", "source_title": "Spaced repetition in early learners"},
        {"finding": "A caregiver's voice measurably increases attention and trust in toddler learning",
         "implication": "parent-recorded audio is the primary prompt channel",
         "source_url": "https://example.com/voice", "source_title": "Caregiver voice studies"},
    ],
    "search_queries_used": ["l2 article acquisition children", "caregiver voice toddler learning"],
}


def _brief():
    from pathlib import Path
    d = json.loads((Path(__file__).parent / "fixtures" / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="s", **d)


def test_domain_pack_wraps():
    p = DomainPack(run_id="r", parent="s", **DOMAIN_GOOD)
    assert p.stage == "domain" and len(p.non_negotiables) == 3


def test_domain_needs_at_least_three_findings():
    bad = json.loads(json.dumps(DOMAIN_GOOD))
    bad["non_negotiables"] = bad["non_negotiables"][:2]
    with pytest.raises(ValidationError, match="non_negotiables"):
        DomainPackDraft.model_validate(bad)


def test_evaluator_enforces_search_and_liveness():
    from pipeline import evaluators
    p = DomainPack(run_id="r", parent="s", web_search_requests=0, **DOMAIN_GOOD)
    reasons = evaluators.evaluate_domain(p, CFG.domain, fetch=lambda url: 200)
    assert any("memory" in r for r in reasons)
    p2 = DomainPack(run_id="r", parent="s", web_search_requests=2, **DOMAIN_GOOD)
    assert evaluators.evaluate_domain(p2, CFG.domain, fetch=lambda url: 200) == []
    reasons = evaluators.evaluate_domain(p2, CFG.domain, fetch=lambda url: 404)
    assert any("dead" in r for r in reasons)


def test_stage_wraps_and_prompts_with_the_subject():
    caller = MockCaller({DomainPackDraft: DOMAIN_GOOD})
    pack, meta = domain.produce(brief=_brief(), parent_sha="sha:aud", caller=caller, cfg=CFG,
                                fetch=lambda url: 200)
    assert pack.parent == "sha:aud" and pack.web_search_requests >= 1
    assert "subject" in caller.calls[0]["user"].lower() or "field" in caller.calls[0]["user"].lower()


def test_crew_gains_domain_station():
    i = VARIANTS["crew"].index
    assert i("audience") < i("domain") < i("panel")
    assert len(expand("crew")) == 19


def test_one_dead_source_among_enough_live_findings_passes():
    from pipeline.contracts import DomainPack
    d = json.loads(json.dumps(DOMAIN_GOOD))
    d["non_negotiables"].append({
        "finding": "Early learners need the same noun repeated across at least three sessions",
        "implication": "words rotate back in until mastered",
        "source_url": "https://rot.example.net/gone", "source_title": "Rotted"})
    p = DomainPack(run_id="r", parent="s", web_search_requests=2, **d)
    from pipeline import evaluators
    def fetch(url):
        return 404 if "rot.example.net" in url else 200
    assert evaluators.evaluate_domain(p, CFG.domain, fetch=fetch) == []
