"""Deterministic per-stage evaluators. No model grades a model here.

Each returns a list of reasons. Empty list means the artifact passes. Schema validity is already
enforced by the contract; these are the checks that need more than one field.
"""
from __future__ import annotations

import re

from urllib.parse import urlparse

from pipeline.config import AudienceRules, EvidenceRules, PanelRules
from pipeline.contracts import (AudiencePack, Brief, BuildResult, DesignSpec, EvidencePack, Plan, ReactionReport,
                                SplitBuildResult, TechSpec, TestReport, UXFlows)
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


DEAD_STATUSES = (404, 410)


def evaluate_evidence(p: EvidencePack, rules: EvidenceRules, *, fetch) -> list[str]:
    """Deterministic gates on researched evidence. `fetch(url) -> status_code` (may raise).

    Liveness is the wiki's own rule: every claim carries a retrievable source. A 403 counts as
    reachable (bot-blocked but alive); 404/410, DNS failures and timeouts are dead.
    The search counter is the anti-fabrication oracle: zero searches means the model quoted
    its memory, however plausible the URLs look."""
    reasons: list[str] = []
    if p.web_search_requests < 1:
        reasons.append("no web search was actually performed; the evidence is the model's memory")
    if len(p.claims) < rules.min_claims:
        reasons.append(f"only {len(p.claims)} claims, need at least {rules.min_claims}")
    domains = {urlparse(c.source_url).netloc for c in p.claims}
    if len(domains) < rules.min_domains:
        reasons.append(f"claims come from {len(domains)} domain(s), need at least {rules.min_domains}")
    checked: dict[str, str | None] = {}
    for c in p.claims:
        if c.source_url not in checked:
            try:
                status = fetch(c.source_url)
                checked[c.source_url] = f"dead ({status})" if status in DEAD_STATUSES else None
            except Exception as e:
                checked[c.source_url] = f"unreachable ({type(e).__name__})"
        problem = checked[c.source_url]
        if problem:
            reasons.append(f"source {problem}: {c.source_url}")
    return reasons


def evaluate_audience(a: AudiencePack, rules: AudienceRules, *, fetch) -> list[str]:
    """Same anti-fabrication oracles as evidence, applied to the audience research."""
    reasons: list[str] = []
    if a.web_search_requests < 1:
        reasons.append("no web search was actually performed; the audience research is the model's memory")
    if len(a.patterns) < rules.min_patterns:
        reasons.append(f"only {len(a.patterns)} patterns, need at least {rules.min_patterns}")
    domains = {urlparse(p.source_url).netloc for p in a.patterns}
    if len(domains) < rules.min_domains:
        reasons.append(f"patterns come from {len(domains)} domain(s), need at least {rules.min_domains}")
    checked: dict[str, str | None] = {}
    for p in a.patterns:
        if p.source_url not in checked:
            try:
                status = fetch(p.source_url)
                checked[p.source_url] = f"dead ({status})" if status in DEAD_STATUSES else None
            except Exception as ex:
                checked[p.source_url] = f"unreachable ({type(ex).__name__})"
        problem = checked[p.source_url]
        if problem:
            reasons.append(f"source {problem}: {p.source_url}")
    return reasons


def evaluate_reaction(rep: ReactionReport, rules: PanelRules) -> list[str]:
    """Re-runs the deterministic arbiter and rejects a report whose verdict does not match.
    The model's scores are inputs; the kill decision must be the rule's, never the model's."""
    from pipeline.stages.panel import arbitrate, is_boundary

    reasons: list[str] = []
    means, kill, kill_reasons = arbitrate(rep.reactions, rules)
    from pipeline.contracts import SEATS
    if len(rep.reactions) == len(SEATS) and is_boundary(means.desirability, rules):
        reasons.append(f"boundary verdict without a confirmation sample: mean desirability "
                       f"{means.desirability:.2f} is within {rules.confirm_margin} of {rules.min_mean_desirability}")
    if rep.means != means:
        reasons.append(f"means tampered: recorded {rep.means}, recomputed {means}")
    if rep.kill != kill or rep.kill_reasons != kill_reasons:
        reasons.append(f"kill verdict tampered: recorded kill={rep.kill}, the rule says kill={kill}")
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


def evaluate_techspec(t: TechSpec, p: Plan) -> list[str]:
    """The TechSpec is the contract the two parallel builders consume. Every interface must name
    one real plan file per scope, and the Brief's api route must appear as an interface."""
    from pipeline.stages.build_split import in_scope

    reasons: list[str] = []
    planned = {f.path for f in p.files}
    for i in t.interfaces:
        for side, path in (("backend", i.backend_file), ("frontend", i.frontend_file)):
            if path not in planned:
                reasons.append(f"interface '{i.name}': {side}_file '{path}' is not in the plan")
            if not in_scope(path, side):
                reasons.append(f"interface '{i.name}': {side}_file '{path}' is outside the {side} scope")
    if not any(i.backend_file.startswith("app/api/") for i in t.interfaces):
        reasons.append("no interface is backed by a route under app/api/; the Brief's api contract is unmapped")
    return reasons


def evaluate_uxflows(u: UXFlows, b: Brief) -> list[str]:
    """Flows are the new signal: every behavior walked through real screens, no dead screens."""
    reasons: list[str] = []
    n = len(b.must_have_behaviors)
    screen_ids = {sc.id for sc in u.screens}
    covered: set[int] = set()
    used: set[str] = set()
    for f in u.flows:
        for i in f.covers_behaviors:
            if not 0 <= i < n:
                reasons.append(f"flow '{f.name}': behavior index {i} out of range 0..{n - 1}")
            else:
                covered.add(i)
        for st in f.steps:
            if st.screen_id not in screen_ids:
                reasons.append(f"flow '{f.name}': step references unknown screen '{st.screen_id}'")
            else:
                used.add(st.screen_id)
    for i in range(n):
        if i not in covered:
            reasons.append(f"behavior {i} is covered by no flow: '{b.must_have_behaviors[i]}'")
    for sc in u.screens:
        if sc.id not in used:
            reasons.append(f"screen '{sc.id}' appears in no flow")
    return reasons


def evaluate_design(d: DesignSpec, b: Brief, components: list[str],
                    ux: UXFlows | None = None) -> list[str]:
    reasons: list[str] = []
    known = set(components)
    n = len(b.must_have_behaviors)
    mapped: set[int] = set()
    for sc in d.screens:
        for c in sc.components_used:
            if c not in known:
                reasons.append(f"screen '{sc.name}' uses unknown component '{c}'")
        for i in sc.maps_behaviors:
            if not 0 <= i < n:
                reasons.append(f"screen '{sc.name}': behavior index {i} out of range 0..{n - 1}")
            else:
                mapped.add(i)
    for i in range(n):
        if i not in mapped:
            reasons.append(f"behavior {i} is mapped to no screen: '{b.must_have_behaviors[i]}'")
    if ux is not None:
        ux_ids = {sc.id for sc in ux.screens}
        covered: set[str] = set()
        for sc in d.screens:
            for sid in sc.covers_screen_ids:
                if sid not in ux_ids:
                    reasons.append(f"screen '{sc.name}' claims unknown ux screen '{sid}'")
                else:
                    covered.add(sid)
        for sid in sorted(ux_ids - covered):
            reasons.append(f"ux screen '{sid}' is not covered by any design screen")
    return reasons


def evaluate_split_build(r: SplitBuildResult, p: Plan | None) -> list[str]:
    from pipeline.stages.build_split import in_scope

    reasons: list[str] = []
    for f in r.overlap:
        reasons.append(f"file written outside a single owner's scope: {f}")
    for part, role in zip(r.parts, r.roles):
        for f in part.files_written:
            if not in_scope(f, role):
                reasons.append(f"{role} wrote out of scope: {f}")
        if part.subtype not in ("success", "error_max_turns"):
            reasons.append(f"{role} builder returned {part.subtype}")
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
