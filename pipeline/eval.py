"""Run the frozen corpus through one graph; one JSONL line per run. Dev runs never write here."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pipeline.contracts import RunManifest


def result_line(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    m = RunManifest.model_validate_json((run_dir / "00-manifest.json").read_text())
    t = m.totals
    line = {
        "run_id": m.run_id, "graph": m.graph, "idea_id": m.idea_id, "status": m.status,
        "failed_stage": m.failed_stage, "verify_pass": False, "tests_passed": None, "tests_total": None,
        "cost_usd": round(t.cost_usd, 6), "billed_usd": round(t.billed_usd, 6), "wall_s": round(t.wall_ms / 1000, 1),
        "input_tokens": t.input_tokens, "output_tokens": t.output_tokens,
        "upstream_rejections": sum(s.upstream_rejections for s in m.stages),
        "build_turns": None, "template_version": m.template_version, "git_sha": m.pipeline_git_sha,
    }
    verifies = sorted(run_dir.glob("*-verify.json"))
    line["repaired"] = bool(list(run_dir.glob("*-repair.json")))
    line["first_verify_pass"] = None
    if verifies:
        final = json.loads(verifies[-1].read_text())
        line.update(verify_pass=bool(final.get("verify_pass")), tests_passed=final.get("tests_passed"),
                    tests_total=final.get("tests_total"))
        if len(verifies) > 1:
            line["first_verify_pass"] = bool(json.loads(verifies[0].read_text()).get("verify_pass"))
    builds = sorted(run_dir.glob("*-build.json"))
    if builds:
        line["build_turns"] = json.loads(builds[0].read_text()).get("num_turns")
    return line


def results_path(root: Path, graph: str) -> Path:
    return Path(root) / "eval" / "results" / f"{graph}.jsonl"


def read_results(root: Path, graph: str) -> list[dict]:
    p = results_path(root, graph)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def ideas_to_run(root: Path, graph: str, *, force: bool) -> list[str]:
    ideas = sorted(p.stem for p in (Path(root) / "corpus" / "ideas").glob("*.md"))
    if force:
        return ideas
    done = {r["idea_id"] for r in read_results(root, graph)}
    return [i for i in ideas if i not in done]


def run_corpus(*, root: Path, graph: str, yes: bool, mock: bool, force: bool, out=sys.stdout) -> int:
    from pipeline.runner import run_one

    root = Path(root)
    todo = ideas_to_run(root, graph, force=force)
    if not todo:
        print(f"nothing to run for {graph}; use --force to re-run", file=out)
        return 0
    path = results_path(root, graph)
    path.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    for idea in todo:
        outcome = run_one(root=root, graph=graph, idea_id=idea, yes=yes, mock=mock, out=out)
        line = result_line(outcome.run_dir)
        with path.open("a") as f:
            f.write(json.dumps(line) + "\n")
        print(f"{graph} {idea}: {line['status']}  verify={'yes' if line['verify_pass'] else 'no'}  "
              f"cost=${line['cost_usd']:.4f}  {line['wall_s']}s", file=out)
        if line["status"] in ("failed", "aborted"):
            failures += 1
    return 1 if failures else 0
