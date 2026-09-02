"""Crew station 10: the Code Reviewer. A program that reads the tree against the template,
the Plan and the TechSpec. It blocks on facts a diff can prove; no model opinion enters,
which is what keeps 'no LLM grades an LLM' true in the crew shape."""
from __future__ import annotations

from pathlib import Path

from pipeline.contracts import (BuildResult, Plan, ReviewFinding, ReviewReport, SplitBuildResult,
                                TechSpec)
from pipeline.contracts import ALLOWED_DIRS
from pipeline.stages.template import LOCKED_FILES, tree_hashes


def produce(*, app_dir: Path, template_dir: Path, build: BuildResult | SplitBuildResult,
            plan: Plan | None, techspec: TechSpec | None, parent_sha: str,
            run_id: str) -> ReviewReport:
    tpl = tree_hashes(Path(template_dir))
    app = tree_hashes(Path(app_dir))
    findings: list[ReviewFinding] = []

    for f in sorted(tpl):
        if f not in app:
            findings.append(ReviewFinding(kind="template_file_deleted", path=f,
                                          detail="present in the template, missing from the app"))
        elif f in LOCKED_FILES and app[f] != tpl[f]:
            findings.append(ReviewFinding(kind="locked_file", path=f, detail="locked template file was modified"))

    expected = {pf.path for pf in plan.files} if plan else set()
    if techspec:
        for i in techspec.interfaces:
            expected.update((i.backend_file, i.frontend_file))
    for f in sorted(expected):
        if f not in app:
            findings.append(ReviewFinding(kind="planned_file_missing", path=f,
                                          detail="planned but never written"))

    for f in sorted(getattr(build, "overlap", []) or []):
        findings.append(ReviewFinding(kind="scope_overlap", path=f,
                                      detail="written outside a single owner's scope"))

    for f in sorted(app):
        if f not in tpl and not f.startswith(ALLOWED_DIRS):
            findings.append(ReviewFinding(kind="stray_file", path=f,
                                          detail="new file outside app/, lib/ or tests/"))

    return ReviewReport(run_id=run_id, parent=parent_sha, findings=findings)
