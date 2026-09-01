import json

import pytest

from pipeline import artifacts
from pipeline.contracts import Brief


def _brief(run_id="r1", parent="sha256:idea"):
    return Brief(
        run_id=run_id, idea_id="01", parent=parent,
        title="URL Word Counter", problem="People want a word count for a page.",
        target_user="Writers", single_feature="Paste a URL, get the count",
        api={"path": "/api/count", "method": "POST", "input_fields": ["url"], "output_fields": ["n"]},
        ui_elements=["input", "button"],
        must_have_behaviors=["Return the count", "Show an error for bad input", "Disable the button while pending"],
        non_goals=["No history", "No PDFs"],
    )


def test_write_names_file_by_sequence_and_stage(tmp_path):
    path, sha = artifacts.write(tmp_path, 1, "brief", _brief())
    assert path.name == "01-brief.json"
    assert sha.startswith("sha256:") and len(sha) == 7 + 64


def test_load_round_trips_and_verifies_hash(tmp_path):
    path, sha = artifacts.write(tmp_path, 1, "brief", _brief())
    got = artifacts.load(path, Brief, expected_sha=sha)
    assert got == _brief()


def test_load_rejects_tampered_file(tmp_path):
    path, sha = artifacts.write(tmp_path, 1, "brief", _brief())
    data = json.loads(path.read_text())
    data["title"] = "Changed"
    path.write_text(json.dumps(data))
    with pytest.raises(artifacts.ArtifactError, match="hash"):
        artifacts.load(path, Brief, expected_sha=sha)


def test_write_never_overwrites(tmp_path):
    artifacts.write(tmp_path, 1, "brief", _brief())
    with pytest.raises(artifacts.ArtifactError, match="exists"):
        artifacts.write(tmp_path, 1, "brief", _brief())


def test_hash_file_matches_written_artifact_sha(tmp_path):
    path, sha = artifacts.write(tmp_path, 1, "brief", _brief())
    assert artifacts.hash_file(path) == sha


def test_load_checks_parent_when_given(tmp_path):
    path, sha = artifacts.write(tmp_path, 1, "brief", _brief(parent="sha256:idea"))
    artifacts.load(path, Brief, expected_parent="sha256:idea")
    with pytest.raises(artifacts.ArtifactError, match="parent"):
        artifacts.load(path, Brief, expected_parent="sha256:other")
