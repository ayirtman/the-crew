"""The template's asset pack is part of the skeleton: every idea may rely on it existing."""
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "templates" / "next-app" / "public" / "assets"


def test_manifest_parses_and_has_five_languages():
    m = json.loads((ASSETS / "manifest.json").read_text())
    assert m["languages"] == ["en", "de", "tr", "es", "fr"]
    assert len(m["words"]) == 20


def test_every_file_the_manifest_names_exists():
    m = json.loads((ASSETS / "manifest.json").read_text())
    missing = []
    for w in m["words"]:
        for ref in [w["image"], *w["audio"].values()]:
            if not (ASSETS.parent / ref.lstrip("/")).exists():
                missing.append(ref)
        assert set(w["audio"]) == set(m["languages"]) and set(w["word"]) == set(m["languages"])
    assert missing == []


def test_words_are_unique_and_lowercase_ids():
    m = json.loads((ASSETS / "manifest.json").read_text())
    ids = [w["id"] for w in m["words"]]
    assert len(set(ids)) == len(ids) and all(i == i.lower() for i in ids)
