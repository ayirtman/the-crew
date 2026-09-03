"""Two deepenings from Batu's first product test (2026-09-03): audience research as its own
station, and a focus group cast per project into seven fixed seats."""
import json

import pytest
from pydantic import ValidationError

from pipeline.contracts import (AudiencePack, AudiencePackDraft, CastingDraft, PersonaReaction,
                                PersonaSpec, ReactionReport, SEATS)
from pipeline.variants import VARIANTS, expand

AUDIENCE_GOOD = {
    "patterns": [
        {"pattern": "Pre-readers need non-textual feedback; wrong answers must sound or move, never explain in words",
         "implication": "wrong answer plays the native-language word and shakes the tile; no error text",
         "source_url": "https://example.org/pre-readers", "source_title": "Designing for pre-readers"},
        {"pattern": "Toddlers tap everything visible; adult controls on the same screen get tapped constantly",
         "implication": "parent stats live behind a separate gated screen, never beside the game",
         "source_url": "https://example.org/toddler-taps", "source_title": "Toddler interaction study"},
        {"pattern": "Session attention for two-year-olds is under five minutes",
         "implication": "session timer defaults under five minutes",
         "source_url": "https://example.com/attention", "source_title": "Attention spans"},
        {"pattern": "Audio prompts must be repeatable on demand because toddlers miss first playback",
         "implication": "a big replay control on the game screen",
         "source_url": "https://example.com/audio", "source_title": "Audio in kids apps"},
    ],
    "constraints": ["the primary user cannot read", "the primary user cannot follow written instructions"],
    "search_queries_used": ["toddler app mistake feedback design", "pre-reader ui patterns"],
}

CAST_GOOD = {"personas": [
    {"seat": s, "name": f"Persona {i}", "description": "A concrete person with a daily life and an opinion about this exact product.",
     "constraints": (["cannot read"] if s == "end_user" else []),
     "grounded_in": ["the primary user cannot read"]}
    for i, s in enumerate(SEATS)
]}


def _reaction(seat, des=4, fea=4):
    return PersonaReaction(persona=seat, scores={"desirability": des, "clarity": 4, "feasibility": fea},
                           objections=["first concrete objection here", "second concrete objection here"],
                           one_change="one change")


def test_seven_seats_exist():
    assert len(SEATS) == 7 and "end_user" in SEATS and "skeptic" in SEATS


def test_audience_pack_wraps():
    a = AudiencePack(run_id="r", parent="s", **AUDIENCE_GOOD)
    assert a.stage == "audience" and len(a.patterns) == 4 and a.web_search_requests == 0


def test_audience_needs_at_least_four_patterns():
    bad = json.loads(json.dumps(AUDIENCE_GOOD))
    bad["patterns"] = bad["patterns"][:2]
    with pytest.raises(ValidationError, match="patterns"):
        AudiencePackDraft.model_validate(bad)


def test_audience_needs_constraints():
    bad = json.loads(json.dumps(AUDIENCE_GOOD))
    bad["constraints"] = []
    with pytest.raises(ValidationError, match="constraints"):
        AudiencePackDraft.model_validate(bad)


def test_casting_requires_exactly_one_persona_per_seat():
    CastingDraft.model_validate(CAST_GOOD)
    bad = json.loads(json.dumps(CAST_GOOD))
    bad["personas"][1]["seat"] = "end_user"
    with pytest.raises(ValidationError, match="seat"):
        CastingDraft.model_validate(bad)


def test_casting_requires_grounding():
    bad = json.loads(json.dumps(CAST_GOOD))
    bad["personas"][0]["grounded_in"] = []
    with pytest.raises(ValidationError, match="grounded_in"):
        CastingDraft.model_validate(bad)


def test_reaction_report_carries_cast_and_seven_reactions():
    cast = CastingDraft.model_validate(CAST_GOOD).personas
    rs = [_reaction(s) for s in SEATS]
    rep = ReactionReport(run_id="r", parent="s", cast=cast, reactions=rs,
                         means={"desirability": 4.0, "clarity": 4.0, "feasibility": 4.0},
                         kill=False, kill_reasons=[])
    assert len(rep.reactions) == 7 and rep.cast[0].seat == SEATS[0]


def test_reaction_report_rejects_missing_seat():
    cast = CastingDraft.model_validate(CAST_GOOD).personas
    rs = [_reaction(s) for s in SEATS[:-1]] + [_reaction(SEATS[0])]
    with pytest.raises(ValidationError, match="seat"):
        ReactionReport(run_id="r", parent="s", cast=cast, reactions=rs,
                       means={"desirability": 4.0, "clarity": 4.0, "feasibility": 4.0},
                       kill=False, kill_reasons=[])


def test_reaction_report_accepts_two_full_samples():
    cast = CastingDraft.model_validate(CAST_GOOD).personas
    rs = [_reaction(s) for s in SEATS] + [_reaction(s, des=3) for s in SEATS]
    rep = ReactionReport(run_id="r", parent="s", cast=cast, reactions=rs,
                         means={"desirability": 3.5, "clarity": 4.0, "feasibility": 4.0},
                         kill=False, kill_reasons=[])
    assert len(rep.reactions) == 14


def test_crew_variant_gains_audience_station():
    assert "audience" in VARIANTS["crew"]
    i = VARIANTS["crew"].index
    assert i("evidence") < i("audience") < i("panel")
    assert len(expand("crew")) == 18
