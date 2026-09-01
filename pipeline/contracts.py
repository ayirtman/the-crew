"""Typed artifact contracts. Every stage boundary is one of these, never prose.

Rules:
- Constraints live in validators, not in Field(min_length=...), so the JSON schema handed to the
  model stays plain and the structured-output endpoint accepts it.
- *Draft models are what the model fills in. The stage wraps a Draft into the full artifact by
  adding the envelope (run_id, stage, parent).
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

BANNED_VAGUE = (
    "user-friendly", "seamless", "intuitive", "robust", "scalable", "innovative",
    "leverage", "various", "etc", "and more", "easy to use", "simple",
)
_VAGUE_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in BANNED_VAGUE) + r")\b", re.I)


def check_vague(text: str, field: str) -> str:
    m = _VAGUE_RE.search(text)
    if m:
        raise ValueError(f"{field}: vague word '{m.group(1)}'")
    return text


def _unique(items: list[str], field: str) -> list[str]:
    seen = set()
    for it in items:
        key = it.strip().lower()
        if key in seen:
            raise ValueError(f"{field}: duplicate entry '{it}'")
        seen.add(key)
    return items


def _count(items: list, field: str, lo: int, hi: int) -> list:
    if not lo <= len(items) <= hi:
        raise ValueError(f"{field}: need between {lo} and {hi} entries, got {len(items)}")
    return items


# ---------------------------------------------------------------- Brief


class ApiContract(BaseModel):
    model_config = STRICT
    path: str
    method: Literal["GET", "POST"]
    input_fields: list[str]
    output_fields: list[str]

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if not v.startswith("/api/"):
            raise ValueError("api.path must start with /api/")
        return v

    @field_validator("output_fields")
    @classmethod
    def _out(cls, v: list[str]) -> list[str]:
        return _count(v, "api.output_fields", 1, 20)


class BriefDraft(BaseModel):
    model_config = STRICT
    title: str
    problem: str
    target_user: str
    single_feature: str
    api: ApiContract
    ui_elements: list[str]
    must_have_behaviors: list[str]
    non_goals: list[str]

    @field_validator("title")
    @classmethod
    def _title(cls, v: str) -> str:
        if len(v) > 60:
            raise ValueError("title: longer than 60 chars")
        return check_vague(v, "title")

    @field_validator("problem")
    @classmethod
    def _problem(cls, v: str) -> str:
        if len(v) > 300:
            raise ValueError("problem: longer than 300 chars")
        return check_vague(v, "problem")

    @field_validator("target_user", "single_feature")
    @classmethod
    def _text(cls, v: str, info) -> str:
        return check_vague(v, info.field_name)

    @field_validator("ui_elements")
    @classmethod
    def _ui(cls, v: list[str]) -> list[str]:
        return _unique(_count(v, "ui_elements", 2, 6), "ui_elements")

    @field_validator("must_have_behaviors")
    @classmethod
    def _behaviors(cls, v: list[str]) -> list[str]:
        _count(v, "must_have_behaviors", 3, 5)
        for b in v:
            check_vague(b, "must_have_behaviors")
        return _unique(v, "must_have_behaviors")

    @field_validator("non_goals")
    @classmethod
    def _non_goals(cls, v: list[str]) -> list[str]:
        return _unique(_count(v, "non_goals", 2, 10), "non_goals")


class Brief(BriefDraft):
    schema_version: Literal["1"] = "1"
    stage: Literal["intake"] = "intake"
    run_id: str
    idea_id: str
    parent: str


# ---------------------------------------------------------------- Plan

ALLOWED_DIRS = ("app/", "lib/", "tests/")


class PlannedFile(BaseModel):
    model_config = STRICT
    path: str
    purpose: str

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if not v.startswith(ALLOWED_DIRS):
            raise ValueError(f"files: '{v}' must be under app/, lib/ or tests/")
        return v


class AcceptanceCriterion(BaseModel):
    model_config = STRICT
    id: str
    behavior_index: int
    statement: str
    test_file: str
    test_name: str

    @field_validator("test_file")
    @classmethod
    def _tf(cls, v: str) -> str:
        if not (v.startswith("tests/") and (v.endswith(".test.ts") or v.endswith(".test.tsx"))):
            raise ValueError(f"test_file: '{v}' must be tests/*.test.ts or .test.tsx")
        return v

    @field_validator("test_name")
    @classmethod
    def _tn(cls, v: str) -> str:
        if not 8 <= len(v) <= 120:
            raise ValueError("test_name: must be 8 to 120 chars")
        return v


class PlanDraft(BaseModel):
    model_config = STRICT
    files: list[PlannedFile]
    acceptance_criteria: list[AcceptanceCriterion]
    build_steps: list[str]

    @field_validator("files")
    @classmethod
    def _files(cls, v: list[PlannedFile]) -> list[PlannedFile]:
        _count(v, "files", 3, 10)
        _unique([f.path for f in v], "files")
        return v

    @field_validator("build_steps")
    @classmethod
    def _steps(cls, v: list[str]) -> list[str]:
        return _count(v, "build_steps", 3, 8)

    @model_validator(mode="after")
    def _criteria(self) -> "PlanDraft":
        _count(self.acceptance_criteria, "acceptance_criteria", 1, 5)
        _unique([c.test_name for c in self.acceptance_criteria], "acceptance_criteria.test_name")
        _unique([c.id for c in self.acceptance_criteria], "acceptance_criteria.id")
        paths = {f.path for f in self.files}
        for c in self.acceptance_criteria:
            if c.test_file not in paths:
                raise ValueError(f"acceptance_criteria: test_file '{c.test_file}' not in files")
        return self


class Plan(PlanDraft):
    schema_version: Literal["1"] = "1"
    stage: Literal["plan"] = "plan"
    run_id: str
    parent: str
    constraints: list[str]


# ---------------------------------------------------------------- Build

class Usage(BaseModel):
    model_config = STRICT
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class BuildResult(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["build"] = "build"
    run_id: str
    parent: str
    app_dir: str
    builder: Literal["claude_code", "fake"]
    model: str
    files_written: list[str]
    subtype: str
    is_error: bool
    num_turns: int
    duration_ms: int
    duration_api_ms: int | None = None
    total_cost_usd_reported: float
    billed_usd: float
    usage: Usage
    permission_denials: int
    session_id: str | None = None
    result_text: str
    exit_code: int

    @classmethod
    def from_claude_json(cls, raw: dict, *, run_id: str, parent: str, app_dir: str, model: str,
                         billed: bool, files_written: list[str], exit_code: int,
                         builder: str = "claude_code") -> "BuildResult":
        u = raw.get("usage") or {}
        cost = float(raw.get("total_cost_usd") or 0.0)
        return cls(
            run_id=run_id, parent=parent, app_dir=app_dir, builder=builder, model=model,
            files_written=files_written,
            subtype=str(raw.get("subtype") or "unknown"),
            is_error=bool(raw.get("is_error", exit_code != 0)),
            num_turns=int(raw.get("num_turns") or 0),
            duration_ms=int(raw.get("duration_ms") or 0),
            duration_api_ms=raw.get("duration_api_ms"),
            total_cost_usd_reported=cost,
            billed_usd=cost if billed else 0.0,
            usage=Usage(
                input_tokens=int(u.get("input_tokens") or 0),
                output_tokens=int(u.get("output_tokens") or 0),
                cache_creation_input_tokens=int(u.get("cache_creation_input_tokens") or 0),
                cache_read_input_tokens=int(u.get("cache_read_input_tokens") or 0),
            ),
            permission_denials=len(raw.get("permission_denials") or []),
            session_id=raw.get("session_id"),
            result_text=str(raw.get("result") or "")[:2000],
            exit_code=exit_code,
        )


# ---------------------------------------------------------------- Verify

CommandName = Literal["tsc", "eslint", "vitest", "next_build"]
TestStatus = Literal["passed", "failed", "skipped", "pending", "todo"]


class CommandResult(BaseModel):
    model_config = STRICT
    name: CommandName
    argv: list[str]
    exit_code: int
    duration_ms: int
    passed: bool
    stdout_tail: str
    stderr_tail: str
    timed_out: bool


class TestCaseResult(BaseModel):
    model_config = STRICT
    file: str
    full_name: str
    title: str
    status: TestStatus
    duration_ms: int


class CriterionCoverage(BaseModel):
    model_config = STRICT
    criterion_id: str
    test_name: str
    found: bool
    status: str | None


class TestReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["verify"] = "verify"
    run_id: str
    parent: str
    commands: list[CommandResult]
    tests: list[TestCaseResult]
    tests_passed: int
    tests_total: int
    eslint_errors: int
    eslint_warnings: int
    min_tests_required: int
    criteria_coverage: list[CriterionCoverage]
    verify_pass: bool = False

    @field_validator("commands")
    @classmethod
    def _four(cls, v: list[CommandResult]) -> list[CommandResult]:
        if len(v) != 4:
            raise ValueError(f"commands: expected four, got {len(v)}")
        return v

    @model_validator(mode="after")
    def _compute_pass(self) -> "TestReport":
        ok = (
            all(c.passed for c in self.commands)
            and self.tests_total >= self.min_tests_required
            and all(c.found and c.status == "passed" for c in self.criteria_coverage)
        )
        object.__setattr__(self, "verify_pass", ok)
        return self


# ---------------------------------------------------------------- Failure and manifest

FailureKind = Literal[
    "schema_invalid", "evaluator_rejected", "budget_exceeded", "subprocess_error", "aborted_by_user",
]


class BudgetSnapshot(BaseModel):
    model_config = STRICT
    tokens_used: int = 0
    tokens_cap: int | None = None
    seconds_used: float = 0.0
    seconds_cap: float | None = None
    cost_used: float = 0.0
    cost_cap: float | None = None


class StageFailure(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: str
    run_id: str
    kind: FailureKind
    reasons: list[str]
    rejected_artifact_path: str | None = None
    budget: BudgetSnapshot | None = None


class StageRecord(BaseModel):
    model_config = STRICT
    stage: str
    artifact_path: str
    artifact_sha256: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    billed_usd: float
    wall_ms: int
    evaluator_passed: bool
    evaluator_reasons: list[str]
    upstream_rejections: int


class Totals(BaseModel):
    model_config = STRICT
    cost_usd: float
    billed_usd: float
    wall_ms: int
    input_tokens: int
    output_tokens: int


RunStatus = Literal["running", "success", "verify_failed", "failed", "aborted"]


class RunManifest(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    run_id: str
    graph: Literal["v0", "v1"]
    idea_id: str
    started_at: str
    finished_at: str | None
    status: RunStatus
    failed_stage: str | None
    stages: list[StageRecord]
    config_snapshot: dict
    pipeline_git_sha: str
    template_version: str
    claude_code_version: str | None

    @property
    def totals(self) -> Totals:
        return Totals(
            cost_usd=sum(s.cost_usd for s in self.stages),
            billed_usd=sum(s.billed_usd for s in self.stages),
            wall_ms=sum(s.wall_ms for s in self.stages),
            input_tokens=sum(s.input_tokens for s in self.stages),
            output_tokens=sum(s.output_tokens for s in self.stages),
        )
