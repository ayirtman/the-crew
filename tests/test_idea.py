from pipeline.idea import parse_idea

PROSE = "a language learning app for toddlers. no gamification.\n"


def test_prose_only_has_no_musts():
    p = parse_idea(PROSE)
    assert p.prose.strip().startswith("a language") and p.musts == [] and p.nevers == []


def test_parses_must_and_never_sections():
    text = PROSE + "\n## Must\n- parent can choose the language\n- each session has a time limit\n\n## Never\n- no rewards\n"
    p = parse_idea(text)
    assert p.musts == ["parent can choose the language", "each session has a time limit"]
    assert p.nevers == ["no rewards"]
    assert "## Must" not in p.prose and "time limit" not in p.prose


def test_tolerates_star_bullets_case_and_blank_lines():
    text = PROSE + "\n## MUST\n* one thing\n\n*  another thing \n## never\n- nothing\n"
    p = parse_idea(text)
    assert p.musts == ["one thing", "another thing"] and p.nevers == ["nothing"]


def test_unknown_headings_stay_in_prose():
    text = PROSE + "\n## Notes\n- keep this in prose\n\n## Must\n- real must\n"
    p = parse_idea(text)
    assert p.musts == ["real must"] and "keep this in prose" in p.prose


def test_normalize_for_matching():
    from pipeline.idea import normalize
    assert normalize("  Each   session has a Time Limit. ") == "each session has a time limit"
