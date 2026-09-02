import json

from pipeline import eval as E
from pipeline import report as R


def _manifest(run_id, graph, idea, status, cost=0.01, billed=0.0, wall=1000):
    return {
        "schema_version": "1", "run_id": run_id, "graph": graph, "idea_id": idea, "started_at": "t",
        "finished_at": "t", "status": status, "failed_stage": None, "config_snapshot": {},
        "pipeline_git_sha": "abc", "template_version": "1", "claude_code_version": None,
        "stages": [{"stage": "build", "artifact_path": "x", "artifact_sha256": "y", "model": "haiku",
                    "input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "cost_usd": cost, "billed_usd": billed, "wall_ms": wall, "evaluator_passed": True,
                    "evaluator_reasons": [], "upstream_rejections": 0}],
    }


def _verify(passed, total):
    return {"tests_passed": passed, "tests_total": total, "verify_pass": passed == total and total > 0}


def _write_run(root, run_id, graph, idea, status, passed=3, total=3):
    d = root / "runs" / run_id
    d.mkdir(parents=True)
    (d / "00-manifest.json").write_text(json.dumps(_manifest(run_id, graph, idea, status)))
    if status in ("success", "verify_failed"):
        (d / "04-verify.json").write_text(json.dumps(_verify(passed, total)))
    return d


def test_result_line_summarises_a_run_dir(tmp_path):
    d = _write_run(tmp_path, "v1-01-x", "v1", "01", "success")
    line = E.result_line(d)
    assert line["graph"] == "v1" and line["idea_id"] == "01" and line["status"] == "success"
    assert line["tests_passed"] == 3 and line["tests_total"] == 3 and line["verify_pass"] is True
    assert line["cost_usd"] == 0.01 and line["wall_s"] == 1.0 and line["upstream_rejections"] == 0


def test_result_line_for_failed_run_has_no_test_counts(tmp_path):
    d = _write_run(tmp_path, "v0-01-x", "v0", "01", "failed")
    line = E.result_line(d)
    assert line["verify_pass"] is False and line["tests_total"] is None


def test_ideas_to_run_skips_ideas_already_in_results_unless_forced(tmp_path):
    (tmp_path / "corpus" / "ideas").mkdir(parents=True)
    for i in ("01", "02", "03"):
        (tmp_path / "corpus" / "ideas" / f"{i}.md").write_text("idea")
    res = tmp_path / "eval" / "results" / "v1.jsonl"
    res.parent.mkdir(parents=True)
    res.write_text(json.dumps({"idea_id": "02", "graph": "v1"}) + "\n")
    assert E.ideas_to_run(tmp_path, "v1", force=False) == ["01", "03"]
    assert E.ideas_to_run(tmp_path, "v1", force=True) == ["01", "02", "03"]


def test_report_joins_results_with_human_scores(tmp_path):
    res = tmp_path / "eval" / "results"
    res.mkdir(parents=True)
    (res / "v0.jsonl").write_text(json.dumps({"run_id": "v0-01-x", "graph": "v0", "idea_id": "01", "status": "verify_failed",
                                              "verify_pass": False, "tests_passed": 1, "tests_total": 3, "cost_usd": 0.0,
                                              "billed_usd": 0.0, "wall_s": 300.0, "input_tokens": 10, "output_tokens": 5,
                                              "upstream_rejections": 0}) + "\n")
    (res / "v1.jsonl").write_text(json.dumps({"run_id": "v1-01-y", "graph": "v1", "idea_id": "01", "status": "success",
                                              "verify_pass": True, "tests_passed": 3, "tests_total": 3, "cost_usd": 0.02,
                                              "billed_usd": 0.0, "wall_s": 400.0, "input_tokens": 20, "output_tokens": 9,
                                              "upstream_rejections": 1}) + "\n")
    (tmp_path / "eval" / "scores.csv").write_text("run_id,d1,d2,d3,d4,d5,notes\nv1-01-y,2,1,2,2,1,fine\n")
    out = R.render(tmp_path)
    assert "| idea | graph | verify | repair | tests | cost_usd | billed_usd | wall_s | tokens | rejections | human |" in out
    assert "| 01 | v0 | no | - | 1/3 |" in out and "| 01 | v1 | yes | - | 3/3 |" in out
    assert "| 8 |" in out and "| - |" in out
    assert "v0: 0/1 verify pass" in out and "v1: 1/1 verify pass" in out


def test_report_discovers_variants_and_counts_repairs_and_kills(tmp_path):
    res = tmp_path / "eval" / "results"
    res.mkdir(parents=True)
    line = {"run_id": "x", "graph": "v1r", "idea_id": "01", "status": "success", "verify_pass": True,
            "tests_passed": 3, "tests_total": 3, "cost_usd": 0.1, "billed_usd": 0.0, "wall_s": 100.0,
            "input_tokens": 1, "output_tokens": 1, "upstream_rejections": 0, "repaired": True,
            "first_verify_pass": False}
    killed = dict(line, run_id="y", idea_id="02", status="killed", verify_pass=False,
                  tests_passed=None, tests_total=None, repaired=False, first_verify_pass=None)
    (res / "v1r.jsonl").write_text("\n".join(__import__("json").dumps(x) for x in (line, killed)) + "\n")
    from pipeline import report as R
    out = R.render(tmp_path)
    assert "v1r: 1/2 verify pass" in out and "1 repaired" in out and "1 killed" in out
    assert "| repair |" in out.splitlines()[0] or "repair" in out.splitlines()[0]
