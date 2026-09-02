"""pipeline.toml -> frozen settings. The config is the only place a cap lives."""
from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

FROZEN = ConfigDict(extra="forbid", frozen=True)


class StageConfig(BaseModel):
    model_config = FROZEN
    model: str | None = None
    max_turns: int | None = None
    max_attempts: int = 1
    max_output_tokens: int | None = None
    max_input_chars: int | None = None
    max_seconds: float
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None
    auth: str | None = None
    tools: str = ""
    per_command_seconds: dict[str, float] = {}


class RunConfig(BaseModel):
    model_config = FROZEN
    max_cost_usd: float
    max_seconds: float


class Price(BaseModel):
    model_config = FROZEN
    input: float
    output: float
    cache_write: float
    cache_read: float


class EvidenceRules(BaseModel):
    model_config = FROZEN
    min_claims: int = 3
    min_domains: int = 2


class PanelRules(BaseModel):
    model_config = FROZEN
    min_mean_desirability: float = 2.5
    kill_feasibility_at_or_below: int = 1


class Config(BaseModel):
    model_config = FROZEN
    stages: dict[str, StageConfig]
    run: RunConfig
    pricing: dict[str, Price]
    evidence: EvidenceRules = EvidenceRules()
    panel: PanelRules = PanelRules()
    root: Path

    def snapshot(self) -> dict:
        return self.model_dump(mode="json", exclude={"root"})


def load_config(path: str | Path = "pipeline.toml") -> Config:
    path = Path(path)
    data = tomllib.loads(path.read_text())
    return Config(**data, root=path.resolve().parent)
