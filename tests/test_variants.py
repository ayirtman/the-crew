import json
from pathlib import Path

import pytest

from pipeline.variants import VARIANTS, expand


def test_known_variants_and_shapes():
    assert VARIANTS["v0"] == ("intake", "build", "verify")
    assert VARIANTS["v1"] == ("intake", "plan", "build", "verify")
    assert VARIANTS["v1r"] == ("intake", "plan", "build", "verify", "repair")
    assert VARIANTS["v2x"][-3:] == ("build_split", "verify", "repair")


def test_expand_inserts_second_verify_after_repair():
    assert expand("v1r") == ("intake", "plan", "build", "verify", "repair", "verify2")
    assert expand("v0") == ("intake", "build", "verify")


def test_unknown_variant_raises():
    with pytest.raises(KeyError):
        expand("v99")


def test_v0_files_are_numbered_by_execution_order(tmp_path):
    from pipeline import graph as G
    from tests.test_graph import _deps, _idea
    G.run(deps=_deps(tmp_path), variant="v0", idea_path=_idea(tmp_path), idea_id="01", run_id="n0", yes=True)
    names = sorted(p.name for p in (tmp_path / "runs" / "n0").glob("0?-*.json"))
    assert names == ["00-manifest.json", "01-brief.json", "02-build.json", "03-verify.json"]


def test_v1r_with_passing_verify_skips_repair(tmp_path):
    from pipeline import graph as G
    from pipeline.contracts import RunManifest
    from tests.test_graph import _deps, _idea
    out = G.run(deps=_deps(tmp_path), variant="v1r", idea_path=_idea(tmp_path), idea_id="01", run_id="n1", yes=True)
    assert out.status == "success"
    run_dir = tmp_path / "runs" / "n1"
    assert not list(run_dir.glob("*-repair.json"))
    assert len(list(run_dir.glob("*-verify.json"))) == 1
    m = RunManifest.model_validate_json((run_dir / "00-manifest.json").read_text())
    assert m.variant_stages == ["intake", "plan", "build", "verify", "repair"]


def test_manifest_graph_accepts_any_variant_name(tmp_path):
    from pipeline.contracts import RunManifest
    m = RunManifest(run_id="r", graph="v2p", idea_id="01", started_at="t", finished_at=None,
                    status="running", failed_stage=None, stages=[], config_snapshot={},
                    pipeline_git_sha="x", template_version="1", claude_code_version=None)
    assert m.graph == "v2p"
