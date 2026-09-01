"""Deterministic per-stage evaluators. No model grades a model here.

Each returns a list of reasons. Empty list means the artifact passes. Schema validity is already
enforced by the contract; these are the checks that need more than one field.
"""
from __future__ import annotations

import re

from pipeline.contracts import Brief, BuildResult, Plan, TestReport
from pipeline.stages.template import LOCKED_FILES

_VERB_HINT = re.compile(r"^(return|show|display|render|disable|enable|reject|accept|validate|"
                        r"save|store|list|count|fetch|send|submit|clear|reset|hide|open|close|"
                        r"allow|prevent|respond|redirect|compute|calculate|sort|filter|update|"
                        r"delete|create|add|remove|toggle|highlight|limit|refuse|warn|report|"
                        r"convert|parse|format|persist|load|export|import|copy|paste|search|"
                        r"select|mark|track|log|print|generate|produce|emit|serve|handle|"
                        r"require|check|confirm|answer|reply|complete|finish|start|stop|"
                        r"run|play|pause|record|keep|set|get|put|post)\b", re.I)


def evaluate_brief(b: Brief) -> list[str]:
    reasons: list[str] = []
    for i, beh in enumerate(b.must_have_behaviors):
        if not _VERB_HINT.match(beh.strip()):
            reasons.append(f"must_have_behaviors[{i}] does not start with a verb: '{beh}'")
    if b.api.method == "POST" and not b.api.input_fields:
        reasons.append("api.input_fields is empty for a POST endpoint")
    return reasons


def evaluate_plan(p: Plan, b: Brief) -> list[str]:
    reasons: list[str] = []
    n = len(b.must_have_behaviors)
    covered: dict[int, int] = {}
    for c in p.acceptance_criteria:
        if not 0 <= c.behavior_index < n:
            reasons.append(f"{c.id}: behavior_index {c.behavior_index} out of range 0..{n - 1}")
            continue
        covered[c.behavior_index] = covered.get(c.behavior_index, 0) + 1
    for i in range(n):
        if covered.get(i, 0) == 0:
            reasons.append(f"behavior {i} has no acceptance criterion: '{b.must_have_behaviors[i]}'")
        elif covered[i] > 1:
            reasons.append(f"behavior {i} has {covered[i]} acceptance criteria, expected one")
    return reasons


def evaluate_build(r: BuildResult, p: Plan | None) -> list[str]:
    reasons: list[str] = []
    if r.is_error or r.subtype != "success":
        reasons.append(f"builder returned {r.subtype}")
    if not r.files_written:
        reasons.append("no files written")
    for f in r.files_written:
        if f in LOCKED_FILES:
            reasons.append(f"locked file edited: {f}")
    if p is not None:
        written = set(r.files_written)
        for tf in sorted({c.test_file for c in p.acceptance_criteria}):
            if tf not in written:
                reasons.append(f"planned test file not written: {tf}")
    return reasons


def evaluate_report(t: TestReport) -> list[str]:
    reasons: list[str] = []
    names = [c.name for c in t.commands]
    if names != ["vitest", "eslint", "next_build", "tsc"]:
        reasons.append(f"commands out of order or missing: {names}")
    for c in t.commands:
        if c.timed_out:
            reasons.append(f"{c.name} timed out")
    return reasons
