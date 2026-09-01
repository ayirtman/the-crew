import json
from pathlib import Path

import pytest

from pipeline import cli, runner
from pipeline.contracts import RunManifest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A throwaway project root with the real config, the tiny template and one idea."""
    (tmp_path / "corpus" / "ideas").mkdir(parents=True)
    (tmp_path / "corpus" / "ideas" / "01.md").write_text("A page where I paste a URL and get its word count.")
    (tmp_path / "pipeline.toml").write_text((ROOT / "pipeline.toml").read_text())
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "TEMPLATE_VERSION").write_text("1")
    (tmp_path / "templates" / "next-app").symlink_to(ROOT / "tests" / "fixtures" / "template_min")
    # no node toolchain in unit tests: swap verify for the canned one
    from tests.test_graph import _deps
    fake = _deps(tmp_path)
    monkeypatch.setattr(runner, "real_verify", fake.verify)
    return tmp_path


def _one_run(project):
    return next((project / "runs").iterdir())


def test_run_mock_v1_produces_full_run_dir(project, capsys):
    rc = cli.main(["run", "--graph", "v1", "--idea", "01", "--yes", "--mock", "--root", str(project)])
    assert rc == 0
    runs = list((project / "runs").iterdir())
    assert len(runs) == 1
    names = sorted(p.name for p in runs[0].glob("0?-*.json"))
    assert names == ["00-manifest.json", "01-brief.json", "02-plan.json", "03-build.json", "04-verify.json"]
    m = RunManifest.model_validate_json((runs[0] / "00-manifest.json").read_text())
    assert m.status == "success" and m.graph == "v1" and m.idea_id == "01"
    assert runs[0].name.startswith("v1-01-")
    assert "success" in capsys.readouterr().out


def test_run_mock_v0_fake_builder_writes_app_good_files(project):
    cli.main(["run", "--graph", "v0", "--idea", "01", "--yes", "--mock", "--root", str(project)])
    app = next((project / "apps").iterdir())
    assert (app / "tests" / "count.test.ts").exists() and (app / "lib" / "count.ts").exists()
    build = json.loads((_one_run(project) / "03-build.json").read_text())
    assert "tests/count.test.ts" in build["files_written"] and build["builder"] == "fake"


def test_run_returns_nonzero_when_the_run_fails(project, monkeypatch):
    monkeypatch.setattr(runner, "MOCK_BRIEF_OVERRIDE", {"problem": "A seamless experience"})
    rc = cli.main(["run", "--graph", "v0", "--idea", "01", "--yes", "--mock", "--root", str(project)])
    assert rc == 1


def test_run_unknown_idea_is_an_error(project):
    with pytest.raises(SystemExit):
        cli.main(["run", "--graph", "v0", "--idea", "99", "--yes", "--mock", "--root", str(project)])


def test_pause_declined_aborts(project, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    rc = cli.main(["run", "--graph", "v0", "--idea", "01", "--mock", "--root", str(project)])
    assert rc == 1
    m = RunManifest.model_validate_json((_one_run(project) / "00-manifest.json").read_text())
    assert m.status == "aborted"


def test_run_accepts_an_idea_file_path_for_dev_ideas(project):
    dev = project / "corpus" / "dev" / "scratch-idea.md"
    dev.parent.mkdir()
    dev.write_text("A scratch idea.")
    rc = cli.main(["run", "--graph", "v0", "--idea", str(dev), "--yes", "--mock", "--root", str(project)])
    assert rc == 0
    run_dir = _one_run(project)
    assert run_dir.name.startswith("v0-scratch-idea-")
    m = RunManifest.model_validate_json((run_dir / "00-manifest.json").read_text())
    assert m.idea_id == "scratch-idea"


def test_eof_at_the_pause_counts_as_decline(project, monkeypatch):
    def eof(_prompt=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    rc = cli.main(["run", "--graph", "v0", "--idea", "01", "--mock", "--root", str(project)])
    assert rc == 1
    m = RunManifest.model_validate_json((_one_run(project) / "00-manifest.json").read_text())
    assert m.status == "aborted"
