from dataclasses import dataclass

from pipeline.contracts import Usage


@dataclass(frozen=True)
class CallMeta:
    model: str
    usage: Usage
    cost_reported: float
    wall_ms: int
    num_turns: int = 0
