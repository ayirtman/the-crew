"""The idea file is the contract. Prose, plus optional '## Must' and '## Never' sections whose
lines the pipeline is forbidden to drop. Parsing is deterministic; enforcement lives in the
Brief evaluator."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HEADING = re.compile(r"^\s*##\s*(\w+)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*\S)\s*$")


@dataclass(frozen=True)
class ParsedIdea:
    prose: str
    musts: list[str] = field(default_factory=list)
    nevers: list[str] = field(default_factory=list)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().strip(".!,;:").casefold().strip()


def parse_idea(text: str) -> ParsedIdea:
    prose_lines: list[str] = []
    musts: list[str] = []
    nevers: list[str] = []
    target: list[str] | None = None
    for line in text.splitlines():
        h = _HEADING.match(line)
        if h:
            name = h.group(1).casefold()
            if name == "must":
                target = musts
                continue
            if name == "never":
                target = nevers
                continue
            target = None  # unknown heading: back to prose
            prose_lines.append(line)
            continue
        if target is not None:
            b = _BULLET.match(line)
            if b:
                target.append(b.group(1).strip())
            elif line.strip():
                target.append(line.strip())
            continue
        prose_lines.append(line)
    return ParsedIdea(prose="\n".join(prose_lines).strip() + "\n", musts=musts, nevers=nevers)
