"""Hard caps. The supervisor holds the budget and never reasons about the product."""
from __future__ import annotations

from dataclasses import dataclass, field

from pipeline.config import Config
from pipeline.contracts import BudgetSnapshot, Usage

# aliases Claude Code accepts on the command line, mapped to the priced snapshot id
MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5-20251001",
}


def canonical_model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


class BudgetExceeded(Exception):
    def __init__(self, reason: str, snapshot: BudgetSnapshot):
        super().__init__(reason)
        self.reason = reason
        self.snapshot = snapshot


def cost_for(usage: Usage, model: str, cfg: Config) -> float:
    price = cfg.pricing.get(canonical_model(model))
    if price is None:
        return 0.0
    per = 1_000_000
    return (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_creation_input_tokens * price.cache_write
        + usage.cache_read_input_tokens * price.cache_read
    ) / per


@dataclass
class Ledger:
    max_cost_usd: float
    max_seconds: float
    cost_used: float = 0.0
    wall_ms: int = 0
    tokens_used: int = 0
    entries: list[dict] = field(default_factory=list)

    def add(self, *, cost_usd: float, wall_ms: int, tokens: int = 0) -> None:
        self.cost_used += cost_usd
        self.wall_ms += wall_ms
        self.tokens_used += tokens
        self.entries.append({"cost_usd": cost_usd, "wall_ms": wall_ms, "tokens": tokens})

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            tokens_used=self.tokens_used, seconds_used=self.wall_ms / 1000,
            seconds_cap=self.max_seconds, cost_used=self.cost_used, cost_cap=self.max_cost_usd,
        )

    def check(self) -> None:
        eps = 1e-9
        if self.cost_used > self.max_cost_usd + eps:
            raise BudgetExceeded(
                f"run cost {self.cost_used:.4f} USD exceeds cap {self.max_cost_usd:.2f}", self.snapshot())
        if self.wall_ms / 1000 > self.max_seconds + eps:
            raise BudgetExceeded(
                f"run wall-clock {self.wall_ms / 1000:.1f} seconds exceeds cap {self.max_seconds:.0f}",
                self.snapshot())
