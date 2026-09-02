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
}


def expand(variant: str) -> tuple[str, ...]:
    """Execution node list: repair implies a second verify right after it."""
    nodes: list[str] = []
    for s in VARIANTS[variant]:
        nodes.append(s)
        if s == "repair":
            nodes.append("verify2")
    return tuple(nodes)
