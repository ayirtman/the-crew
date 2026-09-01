"""Deterministic per-stage evaluators. No model grades a model here.

Each returns a list of reasons. Empty list means the artifact passes. Schema validity is already
enforced by the contract; these are the checks that need more than one field.
"""
from __future__ import annotations

import re

from pipeline.contracts import Brief, BuildResult, Plan, TestReport
from pipeline.idea import ParsedIdea, normalize
from pipeline.stages.template import LOCKED_FILES

# The failure shape seen in practice is a behavior written as a noun phrase or a sentence about
# a subject ("The count...", "Users can..."). A whitelist of verbs rejects good behaviors, so the
# check is the small blocklist of openers that are never an imperative verb.
_NOT_A_VERB = re.compile(r"^(the|a|an|it|its|there|this|that|these|those|user|users|toddler|parent|"
                         r"parents|page|app|system|when|if|should|must|can|will)\b", re.I)


def evaluate_brief(b: Brief, idea: ParsedIdea | None = None) -> list[str]:
    reasons: list[str] = []
    idea = idea or ParsedIdea(prose="")

    # The idea is the contract: every Must and Never line must survive into requirements,
    # and every requirement must actually be covered. Dropping one is a rejectable offence.
    by_norm = {normalize(r.text): r for r in b.requirements}
    n_beh, n_ng = len(b.must_have_behaviors), len(b.non_goals)
    for must in idea.musts:
        r = by_norm.get(normalize(must))
        if r is None or r.kind != "must":
            reasons.append(f"must from the idea is missing from requirements: '{must}'")
    for never in idea.nevers:
        r = by_norm.get(normalize(never))
        if r is None or r.kind != "never":
            reasons.append(f"never from the idea is missing from requirements: '{never}'")
    for r in b.requirements:
        for i in r.covered_by_behaviors:
            if not 0 <= i < n_beh:
                reasons.append(f"requirement '{r.text}': behavior index {i} out of range 0..{n_beh - 1}")
        for i in r.covered_by_non_goals:
            if not 0 <= i < n_ng:
                reasons.append(f"requirement '{r.text}': non_goal index {i} out of range 0..{n_ng - 1}")
        if r.kind == "must" and not any(0 <= i < n_beh for i in r.covered_by_behaviors):
            reasons.append(f"must requirement '{r.text}' is not covered by any behavior")
        if r.kind == "never":
            if not any(0 <= i < n_ng for i in r.covered_by_non_goals):
                reasons.append(f"never requirement '{r.text}' must be covered by a non_goal")
            if r.covered_by_behaviors:
                reasons.append(f"never requirement '{r.text}' maps to behaviors; it belongs in non_goal coverage")
        if r.kind == "prose" and not (r.covered_by_behaviors or r.covered_by_non_goals):
            reasons.append(f"requirement '{r.text}' has no coverage at all")

    for i, beh in enumerate(b.must_have_behaviors):
        if _NOT_A_VERB.match(beh.strip()) or not beh.strip()[:1].isalpha():
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
    # Running out of turns is a spending stop, not a verdict: Verify judges what got written.
    if r.subtype not in ("success", "error_max_turns"):
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
