"""Crew station 10: the Code Reviewer is a program reading the tree. No model opinion enters."""
import json
from pathlib import Path

from pipeline.contracts import (Brief, BuildResult, Plan, SplitBuildResult, TechSpec, Usage)
from pipeline.stages import review
from tests.test_crew_contracts import TECHSPEC_GOOD

FIX = Path(__file__).parent / "fixtures"


def _brief():
    return Brief(run_id="r1", idea_id="01", parent="s", **json.loads((FIX / "brief_good.json").read_text()))


def _plan():
    return Plan(run_id="r1", parent="s", constraints=[], **json.loads((FIX / "plan_good.json").read_text()))


def _techspec():
    return TechSpec(run_id="r1", parent="s", **TECHSPEC_GOOD)


def _part(files, app_dir):
    return BuildResult(run_id="r1", parent="s", app_dir=str(app_dir), builder="fake", model="fake",
                       files_written=files, subtype="success", is_error=False, num_turns=1,
                       duration_ms=1, total_cost_usd_reported=0.0, billed_usd=0.0, usage=Usage(),
                       permission_denials=0, result_text="", exit_code=0)


def _split(app_dir, backend, frontend, overlap=()):
    return SplitBuildResult(run_id="r1", parent="s", app_dir=str(app_dir),
                            parts=[_part(backend, app_dir), _part(frontend, app_dir)],
                            roles=["backend", "frontend"],
                            files_written=sorted({*backend, *frontend, *overlap}), overlap=list(overlap))


def _tree(tmp_path, files, template_extra=None):
    """A minimal template dir and an app dir derived from it plus `files`."""
    tpl = tmp_path / "tpl"
    for f, content in {"package.json": "{}", "app/layout.tsx": "layout",
                       "tests/base.test.ts": "test('base', () => {})",
                       **(template_extra or {})}.items():
        p = tpl / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    app = tmp_path / "app"
    import shutil
    shutil.copytree(tpl, app)
    for f, content in files.items():
        p = app / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tpl, app


GOOD_FILES = {
    "app/page.tsx": "page", "app/api/count/route.ts": "route",
    "lib/count.ts": "lib", "tests/count.test.ts": "test('x', () => {})",
}


def _produce(tpl, app, build):
    return review.produce(app_dir=app, template_dir=tpl, build=build, plan=_plan(),
                          techspec=_techspec(), parent_sha="sha:build", run_id="r1")


def test_clean_build_passes(tmp_path):
    tpl, app = _tree(tmp_path, GOOD_FILES)
    rep = _produce(tpl, app, _split(app, ["app/api/count/route.ts", "lib/count.ts"],
                                    ["app/page.tsx"], ()))
    assert rep.review_pass is True and rep.findings == [] and rep.parent == "sha:build"


def test_locked_file_modification_is_a_finding(tmp_path):
    tpl, app = _tree(tmp_path, {**GOOD_FILES, "package.json": '{"extra": true}'})
    rep = _produce(tpl, app, _split(app, [], ["app/page.tsx"]))
    assert any(f.kind == "locked_file" and f.path == "package.json" for f in rep.findings)
    assert rep.review_pass is False


def test_deleted_template_file_is_a_finding(tmp_path):
    tpl, app = _tree(tmp_path, GOOD_FILES)
    (app / "tests/base.test.ts").unlink()
    rep = _produce(tpl, app, _split(app, [], ["app/page.tsx"]))
    assert any(f.kind == "template_file_deleted" and f.path == "tests/base.test.ts" for f in rep.findings)


def test_planned_file_missing_is_a_finding(tmp_path):
    files = dict(GOOD_FILES)
    del files["lib/count.ts"]
    tpl, app = _tree(tmp_path, files)
    rep = _produce(tpl, app, _split(app, ["app/api/count/route.ts"], ["app/page.tsx"]))
    assert any(f.kind == "planned_file_missing" and f.path == "lib/count.ts" for f in rep.findings)


def test_scope_overlap_from_the_split_build_is_a_finding(tmp_path):
    tpl, app = _tree(tmp_path, GOOD_FILES)
    rep = _produce(tpl, app, _split(app, [], ["app/page.tsx"], overlap=["app/rogue.css"]))
    assert any(f.kind == "scope_overlap" and f.path == "app/rogue.css" for f in rep.findings)


def test_stray_file_outside_allowed_dirs_is_a_finding(tmp_path):
    tpl, app = _tree(tmp_path, {**GOOD_FILES, "scripts/hack.sh": "#!/bin/sh"})
    rep = _produce(tpl, app, _split(app, [], ["app/page.tsx"]))
    assert any(f.kind == "stray_file" and f.path == "scripts/hack.sh" for f in rep.findings)


def test_plain_build_result_works_without_overlap_semantics(tmp_path):
    tpl, app = _tree(tmp_path, GOOD_FILES)
    b = _part(sorted(GOOD_FILES), app)
    rep = review.produce(app_dir=app, template_dir=tpl, build=b, plan=_plan(), techspec=None,
                         parent_sha="s", run_id="r1")
    assert rep.review_pass is True


def test_review_is_idempotent_after_a_repair_pass(tmp_path):
    # review2 re-reads the disk: a repair that restores the deleted file clears the finding
    tpl, app = _tree(tmp_path, GOOD_FILES)
    (app / "tests/base.test.ts").unlink()
    split = _split(app, [], ["app/page.tsx"])
    assert _produce(tpl, app, split).review_pass is False
    (app / "tests/base.test.ts").write_text("test('base', () => {})")
    assert _produce(tpl, app, split).review_pass is True


def test_toolchain_generated_files_are_not_strays(tmp_path):
    # `next build` (run by verify, before review2) generates next-env.d.ts in the app dir;
    # a file the toolchain writes is not the builder's doing and must never fail review.
    tpl, app = _tree(tmp_path, {**GOOD_FILES, "next-env.d.ts": "/// <reference types=\"next\" />"})
    rep = _produce(tpl, app, _split(app, [], ["app/page.tsx"]))
    assert rep.review_pass is True
