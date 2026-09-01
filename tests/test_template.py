import hashlib
from pathlib import Path

import pytest

from pipeline.stages import template

ROOT = Path(__file__).resolve().parents[1]
TPL = Path(__file__).parent / "fixtures" / "template_min"


def test_materialize_copies_template_into_run_app_dir(tmp_path):
    app = template.materialize(TPL, tmp_path / "apps", "run-1")
    assert app == tmp_path / "apps" / "run-1"
    assert (app / "package.json").read_bytes() == (TPL / "package.json").read_bytes()
    assert (app / "vitest.config.mts").exists()


def test_materialize_refuses_existing_dir(tmp_path):
    template.materialize(TPL, tmp_path / "apps", "run-1")
    with pytest.raises(FileExistsError):
        template.materialize(TPL, tmp_path / "apps", "run-1")


def test_two_materializations_are_independent(tmp_path):
    a = template.materialize(TPL, tmp_path / "apps", "a")
    b = template.materialize(TPL, tmp_path / "apps", "b")
    (a / "app" / "page.tsx").write_text("changed")
    assert (b / "app" / "page.tsx").read_text() != "changed"


def test_tree_hashes_lists_files_relative_and_skips_node_modules(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "x.ts").write_text("a")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "y.js").write_text("b")
    (tmp_path / ".next").mkdir()
    (tmp_path / ".next" / "z").write_text("c")
    (tmp_path / "tsconfig.tsbuildinfo").write_text("d")
    h = template.tree_hashes(tmp_path)
    assert set(h) == {"app/x.ts"}
    assert h["app/x.ts"] == hashlib.sha256(b"a").hexdigest()


def test_diff_files_reports_added_and_changed_only():
    before = {"a": "1", "b": "2"}
    after = {"a": "1", "b": "3", "c": "4"}
    assert template.diff_files(before, after) == ["b", "c"]


def test_locked_files_are_the_config_surface():
    assert "package.json" in template.LOCKED_FILES
    assert "vitest.config.mts" in template.LOCKED_FILES
    assert "next.config.ts" in template.LOCKED_FILES


@pytest.mark.slow
def test_real_template_materializes_with_a_real_node_modules(tmp_path):
    app = template.materialize(ROOT / "templates" / "next-app", tmp_path / "apps", "real")
    assert (app / "node_modules" / ".bin" / "next").exists()
    assert not (app / "node_modules").is_symlink()
