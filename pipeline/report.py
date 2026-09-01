"""The v0 vs v1 table. Machine numbers from results/, the human score from scores.csv."""
from __future__ import annotations

import csv
from pathlib import Path

from pipeline.eval import read_results

HEADER = "| idea | graph | verify | tests | cost_usd | billed_usd | wall_s | tokens | rejections | human |"


def read_scores(root: Path) -> dict[str, int]:
    p = Path(root) / "eval" / "scores.csv"
    if not p.exists():
        return {}
    out: dict[str, int] = {}
    with p.open() as f:
        for row in csv.DictReader(f):
            try:
                out[row["run_id"]] = sum(int(row[f"d{i}"]) for i in range(1, 6))
            except (KeyError, ValueError):
                continue
    return out


def render(root: Path) -> str:
    root = Path(root)
    scores = read_scores(root)
    rows = []
    summary = []
    for graph in ("v0", "v1"):
        res = read_results(root, graph)
        if not res:
            continue
        passes = sum(1 for r in res if r.get("verify_pass"))
        cost = sum(float(r.get("cost_usd") or 0) for r in res)
        scored = [scores[r["run_id"]] for r in res if r["run_id"] in scores]
        human = f"{sum(scored)}/{10 * len(scored)}" if scored else "-"
        summary.append(f"{graph}: {passes}/{len(res)} verify pass, cost ${cost:.4f}, human {human}")
        for r in res:
            tests = f"{r['tests_passed']}/{r['tests_total']}" if r.get("tests_total") is not None else "-"
            tokens = int(r.get("input_tokens") or 0) + int(r.get("output_tokens") or 0)
            rows.append((r["idea_id"], graph,
                         f"| {r['idea_id']} | {graph} | {'yes' if r.get('verify_pass') else 'no'} | {tests} | "
                         f"{float(r.get('cost_usd') or 0):.4f} | {float(r.get('billed_usd') or 0):.4f} | "
                         f"{r.get('wall_s', 0)} | {tokens} | {r.get('upstream_rejections', 0)} | "
                         f"{scores.get(r['run_id'], '-')} |"))
    rows.sort()
    lines = [HEADER, "|---|---|---|---|---|---|---|---|---|---|"] + [r[2] for r in rows]
    if summary:
        lines += ["", *summary]
    return "\n".join(lines)
