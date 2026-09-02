"""Step 1 of the crew build: contracts for the five new artifacts and the crew variant shape."""
import json

import pytest
from pydantic import ValidationError

from pipeline.contracts import (AnalyticsReport, ReviewFinding, ReviewReport, RunManifest,
                                SecurityFinding, SecurityReport, TechSpec, TechSpecDraft,
                                UrlCheck, UXFlows, UXFlowsDraft)
from pipeline.variants import VARIANTS, expand

TECHSPEC_GOOD = {
    "entities": [{"name": "CountResult", "fields": ["word_count", "title"]}],
    "interfaces": [{
        "name": "count api",
        "backend_file": "app/api/count/route.ts",
        "frontend_file": "app/page.tsx",
        "shape": ["POST /api/count {url} -> {word_count, title}"],
    }],
}

UX_GOOD = {
    "screens": [{"id": "main", "name": "Main", "purpose": "paste a url and read the count"}],
    "flows": [{
        "name": "count a url",
        "covers_behaviors": [0, 1, 2],
        "steps": [{"screen_id": "main", "action": "paste a url and press the count button"}],
    }],
}


# ---------------------------------------------------------------- variant shape


def test_crew_variant_matches_the_diagram():
    assert VARIANTS["crew"] == (
        "intake", "evidence", "panel", "plan", "architect", "ux", "ui",
        "build_split", "review", "verify", "security", "repair", "ship", "analytics")


def test_expand_crew_inserts_the_verify_trio_after_repair():
    nodes = expand("crew")
    i = nodes.index("repair")
    assert nodes[i + 1:i + 4] == ("review2", "verify2", "security2")
    assert nodes[-2:] == ("ship", "analytics")


def test_expand_old_variants_unchanged():
    assert expand("v1r") == ("intake", "plan", "build", "verify", "repair", "verify2")
    assert expand("v0") == ("intake", "build", "verify")


# ---------------------------------------------------------------- TechSpec


def test_techspec_wraps_and_holds():
    t = TechSpec(run_id="r", parent="sha", **TECHSPEC_GOOD)
    assert t.stage == "architect" and t.interfaces[0].backend_file.startswith("app/api/")


def test_techspec_rejects_empty_interfaces():
    with pytest.raises(ValidationError, match="interfaces"):
        TechSpecDraft.model_validate({"entities": TECHSPEC_GOOD["entities"], "interfaces": []})


def test_techspec_rejects_duplicate_entity_names():
    bad = json.loads(json.dumps(TECHSPEC_GOOD))
    bad["entities"] = [{"name": "A", "fields": ["x"]}, {"name": "a", "fields": ["y"]}]
    with pytest.raises(ValidationError, match="duplicate"):
        TechSpecDraft.model_validate(bad)


def test_techspec_rejects_illegal_file_path():
    bad = json.loads(json.dumps(TECHSPEC_GOOD))
    bad["interfaces"][0]["backend_file"] = "package.json"
    with pytest.raises(ValidationError, match="app/"):
        TechSpecDraft.model_validate(bad)


# ---------------------------------------------------------------- UXFlows


def test_uxflows_wraps_and_holds():
    u = UXFlows(run_id="r", parent="sha", **UX_GOOD)
    assert u.stage == "ux" and u.flows[0].steps[0].screen_id == "main"


def test_uxflows_rejects_duplicate_screen_ids():
    bad = json.loads(json.dumps(UX_GOOD))
    bad["screens"].append(dict(bad["screens"][0]))
    with pytest.raises(ValidationError, match="duplicate"):
        UXFlowsDraft.model_validate(bad)


def test_uxflows_rejects_empty_flows():
    with pytest.raises(ValidationError, match="flows"):
        UXFlowsDraft.model_validate({"screens": UX_GOOD["screens"], "flows": []})


# ---------------------------------------------------------------- ReviewReport / SecurityReport


def test_review_pass_is_computed_from_findings():
    ok = ReviewReport(run_id="r", parent="s", findings=[])
    assert ok.review_pass is True
    bad = ReviewReport(run_id="r", parent="s", findings=[
        ReviewFinding(kind="locked_file", path="package.json", detail="modified")])
    assert bad.review_pass is False and bad.stage == "review"


def test_review_pass_cannot_be_forged():
    # same rule as TestReport.verify_pass: the computed value overrides whatever was written
    r = ReviewReport.model_validate({"run_id": "r", "parent": "s", "findings": [
        {"kind": "locked_file", "path": "p", "detail": "d"}], "review_pass": True})
    assert r.review_pass is False


def test_security_pass_is_computed_from_findings():
    ok = SecurityReport(run_id="r", parent="s", findings=[], audit_ran=True)
    assert ok.security_pass is True and ok.stage == "security"
    bad = SecurityReport(run_id="r", parent="s", audit_ran=False, findings=[
        SecurityFinding(kind="secret", path="lib/x.ts", detail="api key literal")])
    assert bad.security_pass is False


# ---------------------------------------------------------------- AnalyticsReport


def test_analytics_pass_requires_all_200s():
    ok = AnalyticsReport(run_id="r", parent="s", url="https://x.vercel.app",
                         checks=[UrlCheck(url="https://x.vercel.app", status=200, response_ms=80, bytes=1200)])
    assert ok.analytics_pass is True and ok.stage == "analytics"
    bad = AnalyticsReport(run_id="r", parent="s", url="https://x.vercel.app",
                          checks=[UrlCheck(url="https://x.vercel.app", status=500, response_ms=80, bytes=0)])
    assert bad.analytics_pass is False


# ---------------------------------------------------------------- manifest status


def test_manifest_accepts_verified_unshipped():
    m = RunManifest(run_id="r", graph="crew", idea_id="01", started_at="t", finished_at="t",
                    status="verified_unshipped", failed_stage=None, stages=[], config_snapshot={},
                    pipeline_git_sha="x", template_version="3", claude_code_version=None)
    assert m.status == "verified_unshipped"
