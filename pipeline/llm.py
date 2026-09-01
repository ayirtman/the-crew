"""Structured calls for Intake and Plan. Default: Claude Code headless on the subscription.

`claude -p --json-schema --tools ""` returns a `structured_output` object. No tools, no persona,
one call, one typed object out. The Anthropic SDK caller is the API-key alternative; same protocol.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from pipeline.budget import BudgetExceeded
from pipeline.config import StageConfig
from pipeline.contracts import BudgetSnapshot, Usage

T = TypeVar("T", bound=BaseModel)

# env vars that would make the CLI bill an API key or refuse to nest inside a Claude Code session
STRIP_ENV = ("ANTHROPIC_API_KEY", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")


class CallerError(Exception):
    pass


class SchemaInvalid(Exception):
    def __init__(self, reasons: list[str], raw: dict | None = None):
        super().__init__("; ".join(reasons))
        self.reasons = reasons
        self.raw = raw


@dataclass(frozen=True)
class CallResult:
    parsed: BaseModel
    usage: Usage
    cost_reported: float
    num_turns: int
    duration_ms: int
    raw: dict


class StructuredCaller(Protocol):
    def call(self, *, system_file: Path, user: str, schema: type[T], stage: StageConfig) -> CallResult: ...


@dataclass(frozen=True)
class RetryResult:
    parsed: BaseModel
    usage: Usage
    cost_reported: float
    num_turns: int
    duration_ms: int
    attempts: int
    rejections: list[list[str]]


def call_with_retry(caller: StructuredCaller, *, system_file: Path, user: str, schema: type[T],
                    stage: StageConfig, attempts: int) -> RetryResult:
    """One bounded retry loop around a structured call.

    A draft that breaks the contract is sent back with the reasons, at most `attempts` times in
    total. The gate itself does not soften: after the last attempt the SchemaInvalid propagates.
    Usage and cost are summed across attempts so the ledger sees the real price.
    """
    usage = Usage()
    cost, turns, ms = 0.0, 0, 0
    rejections: list[list[str]] = []
    prompt = user
    for i in range(1, max(1, attempts) + 1):
        try:
            r = caller.call(system_file=system_file, user=prompt, schema=schema, stage=stage)
        except SchemaInvalid as e:
            rejections.append(e.reasons)
            raw = e.raw or {}
            u = usage_from(raw)
            usage = Usage(input_tokens=usage.input_tokens + u.input_tokens,
                          output_tokens=usage.output_tokens + u.output_tokens,
                          cache_creation_input_tokens=usage.cache_creation_input_tokens + u.cache_creation_input_tokens,
                          cache_read_input_tokens=usage.cache_read_input_tokens + u.cache_read_input_tokens)
            cost += float(raw.get("total_cost_usd") or 0.0)
            turns += int(raw.get("num_turns") or 0)
            ms += int(raw.get("duration_ms") or 0)
            if i == attempts:
                raise
            prompt = (user + "\n\nYour previous answer was rejected by the contract for these reasons:\n"
                      + "\n".join(f"- {x}" for x in e.reasons)
                      + "\nFix every one of them and answer again. Keep everything else the same.")
            continue
        usage = Usage(input_tokens=usage.input_tokens + r.usage.input_tokens,
                      output_tokens=usage.output_tokens + r.usage.output_tokens,
                      cache_creation_input_tokens=usage.cache_creation_input_tokens + r.usage.cache_creation_input_tokens,
                      cache_read_input_tokens=usage.cache_read_input_tokens + r.usage.cache_read_input_tokens)
        return RetryResult(parsed=r.parsed, usage=usage, cost_reported=cost + r.cost_reported,
                           num_turns=turns + r.num_turns, duration_ms=ms + r.duration_ms, attempts=i,
                           rejections=rejections)
    raise AssertionError("unreachable")


Runner = Callable[..., subprocess.CompletedProcess]


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in STRIP_ENV}
    env["CI"] = "1"
    return env


def _validation_reasons(e: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in err['loc']) or 'root'}: {err['msg']}" for err in e.errors()]


def usage_from(raw: dict) -> Usage:
    u = raw.get("usage") or {}
    return Usage(
        input_tokens=int(u.get("input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cache_creation_input_tokens=int(u.get("cache_creation_input_tokens") or 0),
        cache_read_input_tokens=int(u.get("cache_read_input_tokens") or 0),
    )


class ClaudeCliCaller:
    def __init__(self, runner: Runner = subprocess.run, cwd: Path | None = None):
        self.runner = runner
        self.cwd = cwd

    def argv(self, *, system_file: Path, user: str, schema: type[BaseModel], stage: StageConfig) -> list[str]:
        return [
            "claude", "-p", user,
            "--output-format", "json",
            "--json-schema", json.dumps(schema.model_json_schema()),
            "--tools", "",
            "--model", stage.model or "haiku",
            "--max-turns", str(stage.max_turns or 2),
            "--safe-mode", "--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands",
            "--append-system-prompt-file", str(system_file),
        ]

    def call(self, *, system_file: Path, user: str, schema: type[T], stage: StageConfig) -> CallResult:
        argv = self.argv(system_file=system_file, user=user, schema=schema, stage=stage)
        t0 = time.monotonic()
        try:
            proc = self.runner(argv, cwd=self.cwd, env=clean_env(), timeout=stage.max_seconds,
                               capture_output=True, text=True, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise BudgetExceeded(
                f"stage wall-clock exceeded {stage.max_seconds} seconds",
                BudgetSnapshot(seconds_used=time.monotonic() - t0, seconds_cap=stage.max_seconds))
        if proc.returncode != 0:
            raise CallerError(f"claude exited {proc.returncode}: {(proc.stderr or proc.stdout or '')[-800:]}")
        try:
            raw = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise CallerError(f"claude stdout is not JSON: {proc.stdout[-400:]}") from e
        if raw.get("is_error"):
            raise CallerError(f"claude reported {raw.get('subtype')}: {str(raw.get('result'))[:400]}")
        structured = raw.get("structured_output")
        if structured is None:
            raise SchemaInvalid(["no structured_output in result"], raw)
        try:
            parsed = schema.model_validate(structured)
        except ValidationError as e:
            raise SchemaInvalid(_validation_reasons(e), raw) from e
        return CallResult(
            parsed=parsed, usage=usage_from(raw), cost_reported=float(raw.get("total_cost_usd") or 0.0),
            num_turns=int(raw.get("num_turns") or 0),
            duration_ms=int(raw.get("duration_ms") or (time.monotonic() - t0) * 1000), raw=raw,
        )


class MockCaller:
    """Returns canned drafts per schema. Zero cost, zero tokens, for tests and --mock runs."""

    def __init__(self, responses: dict[type[BaseModel], dict]):
        self.responses = responses
        self.calls: list[dict] = []

    def call(self, *, system_file: Path, user: str, schema: type[T], stage: StageConfig) -> CallResult:
        self.calls.append({"system_file": system_file, "user": user, "schema": schema})
        try:
            parsed = schema.model_validate(self.responses[schema])
        except ValidationError as e:
            raise SchemaInvalid(_validation_reasons(e)) from e
        return CallResult(parsed=parsed, usage=Usage(), cost_reported=0.0, num_turns=1, duration_ms=1,
                          raw={"mock": True})
