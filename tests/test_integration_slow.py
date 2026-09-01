"""Real node toolchain, no LLM. Run with: pytest -m slow"""
from pathlib import Path

import pytest

from pipeline import cli
from pipeline.contracts import TestReport as Report

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.slow


@pytest.fixture
def project(tmp_path):
    (tmp_path / "corpus" / "ideas").mkdir(parents=True)
    (tmp_path / "corpus" / "ideas" / "01.md").write_text("A page where I paste a URL and get its word count.")
    (tmp_path / "pipeline.toml").write_text((ROOT / "pipeline.toml").read_text())
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "TEMPLATE_VERSION").write_text("1")
    (tmp_path / "templates" / "next-app").symlink_to(ROOT / "templates" / "next-app")
    return tmp_path


def test_mock_v1_run_passes_real_verify_end_to_end(project):
    rc = cli.main(["run", "--graph", "v1", "--idea", "01", "--yes", "--mock", "--root", str(project)])
    assert rc == 0
    run_dir = next((project / "runs").iterdir())
    rep = Report.model_validate_json((run_dir / "04-verify.json").read_text())
    failed = [(c.name, c.stderr_tail[-600:], c.stdout_tail[-600:]) for c in rep.commands if not c.passed]
    assert failed == [], failed
    assert rep.tests_total == 3 and rep.tests_passed == 3
    assert all(c.found and c.status == "passed" for c in rep.criteria_coverage)
    assert rep.verify_pass is True
