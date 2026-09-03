"""Named graph variants: an ordered stage list per name. Stage semantics live in graph.py;
this module only declares shapes, so eval and cli can name any variant."""
from __future__ import annotations

VARIANTS: dict[str, tuple[str, ...]] = {
    "v0": ("intake", "build", "verify"),
    "v1": ("intake", "plan", "build", "verify"),
    "v1r": ("intake", "plan", "build", "verify", "repair"),
    "v2e": ("intake", "evidence", "plan", "build", "verify", "repair"),
    "v2p": ("intake", "evidence", "panel", "plan", "build", "verify", "repair"),
    "v2d": ("intake", "evidence", "panel", "plan", "design", "build", "verify", "repair"),
    "v2x": ("intake", "evidence", "panel", "plan", "design", "build_split", "verify", "repair"),
    # the-crew.svg, compiled: all 14 stations. review/verify/security are programs, not models.
    "crew": ("intake", "evidence", "audience", "panel", "plan", "architect", "ux", "ui",
             "build_split", "review", "verify", "security", "repair", "ship", "analytics"),
}

# the red boxes: every verifier present in a variant re-runs, in this order, after repair
VERIFIERS = ("review", "verify", "security")


def expand(variant: str) -> tuple[str, ...]:
    """Execution node list: repair implies a second pass of every verifier in the variant."""
    stages = VARIANTS[variant]
    trio = [s for s in VERIFIERS if s in stages]
    nodes: list[str] = []
    for s in stages:
        nodes.append(s)
        if s == "repair":
            nodes.extend(f"{t}2" for t in trio)
    return tuple(nodes)
