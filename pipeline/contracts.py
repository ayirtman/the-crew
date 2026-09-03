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


class Requirement(BaseModel):
    model_config = STRICT
    text: str
    kind: Literal["must", "never", "prose"]
    covered_by_behaviors: list[int]
    covered_by_non_goals: list[int]


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
    requirements: list[Requirement] = []

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
        return _unique(_count(v, "ui_elements", 2, 8), "ui_elements")

    @field_validator("must_have_behaviors")
    @classmethod
    def _behaviors(cls, v: list[str]) -> list[str]:
        _count(v, "must_have_behaviors", 3, 8)
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

ALLOWED_DIRS = ("app/", "lib/", "tests/", "components/")


class PlannedFile(BaseModel):
    model_config = STRICT
    path: str
    purpose: str

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        if not v.startswith(ALLOWED_DIRS):
            raise ValueError(f"files: '{v}' must be under app/, lib/ or tests/")
        if v.startswith("tests/ui/") and v.endswith(".test.ts"):
            raise ValueError(f"files: '{v}' renders JSX; ui tests must end in .test.tsx")
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
        _count(v, "files", 3, 12)
        _unique([f.path for f in v], "files")
        return v

    @field_validator("build_steps")
    @classmethod
    def _steps(cls, v: list[str]) -> list[str]:
        return _count(v, "build_steps", 3, 8)

    @model_validator(mode="after")
    def _criteria(self) -> "PlanDraft":
        _count(self.acceptance_criteria, "acceptance_criteria", 1, 8)
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


# ---------------------------------------------------------------- Evidence


def _http(v: str, field: str) -> str:
    if not (v.startswith("http://") or v.startswith("https://")):
        raise ValueError(f"{field}: '{v}' must be an http(s) url")
    return v


class EvidenceClaim(BaseModel):
    model_config = STRICT
    statement: str
    source_url: str
    source_title: str
    retrieved: bool

    @field_validator("statement")
    @classmethod
    def _stmt(cls, v: str) -> str:
        if not 10 <= len(v) <= 300:
            raise ValueError("statement: must be 10 to 300 chars")
        return check_vague(v, "statement")

    @field_validator("source_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _http(v, "source_url")


class Competitor(BaseModel):
    model_config = STRICT
    name: str
    url: str
    note: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _http(v, "url")


class EvidencePackDraft(BaseModel):
    model_config = STRICT
    claims: list[EvidenceClaim]
    competitors: list[Competitor]
    search_queries_used: list[str]

    @field_validator("claims")
    @classmethod
    def _claims(cls, v):
        return _count(v, "claims", 1, 20)

    @field_validator("competitors")
    @classmethod
    def _comp(cls, v):
        _count(v, "competitors", 0, 8)
        _unique([c.name for c in v], "competitors")
        return v

    @field_validator("search_queries_used")
    @classmethod
    def _q(cls, v):
        return _count(v, "search_queries_used", 1, 12)


class EvidencePack(EvidencePackDraft):
    schema_version: Literal["1"] = "1"
    stage: Literal["evidence"] = "evidence"
    run_id: str
    parent: str
    web_search_requests: int = 0


# ---------------------------------------------------------------- Audience


class UxPattern(BaseModel):
    model_config = STRICT
    pattern: str
    implication: str
    source_url: str
    source_title: str

    @field_validator("pattern", "implication")
    @classmethod
    def _texts(cls, v: str, info) -> str:
        if not 10 <= len(v) <= 300:
            raise ValueError(f"{info.field_name}: must be 10 to 300 chars")
        return check_vague(v, info.field_name)

    @field_validator("source_url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _http(v, "source_url")


class AudiencePackDraft(BaseModel):
    """How this audience actually interacts: mistake handling, attention, distraction. The
    research the designers and builders read before anything is drawn."""
    model_config = STRICT
    patterns: list[UxPattern]
    constraints: list[str]
    search_queries_used: list[str]

    @field_validator("patterns")
    @classmethod
    def _patterns(cls, v):
        return _count(v, "patterns", 4, 12)

    @field_validator("constraints")
    @classmethod
    def _constraints(cls, v):
        return _unique(_count(v, "constraints", 1, 8), "constraints")

    @field_validator("search_queries_used")
    @classmethod
    def _q(cls, v):
        return _count(v, "search_queries_used", 1, 12)


class AudiencePack(AudiencePackDraft):
    schema_version: Literal["1"] = "1"
    stage: Literal["audience"] = "audience"
    run_id: str
    parent: str
    web_search_requests: int = 0


# ---------------------------------------------------------------- Panel

# The focus group: seven fixed seats whose identities are cast per project from the research.
SEATS = ("end_user", "buyer", "skeptic", "operator", "domain_expert", "edge_user", "rival_user")
Seat = Literal["end_user", "buyer", "skeptic", "operator", "domain_expert", "edge_user", "rival_user"]


class PersonaSpec(BaseModel):
    """One cast panelist: a concrete person occupying a fixed seat, grounded in the research."""
    model_config = STRICT
    seat: Seat
    name: str
    description: str
    constraints: list[str]
    grounded_in: list[str]

    @field_validator("description")
    @classmethod
    def _desc(cls, v: str) -> str:
        if len(v) < 40:
            raise ValueError("description: shorter than 40 chars; cast a real person, not a label")
        return v

    @field_validator("grounded_in")
    @classmethod
    def _g(cls, v):
        return _count(v, "grounded_in", 1, 6)


class CastingDraft(BaseModel):
    model_config = STRICT
    personas: list[PersonaSpec]

    @model_validator(mode="after")
    def _seats(self) -> "CastingDraft":
        seats = [p.seat for p in self.personas]
        if sorted(seats) != sorted(SEATS):
            raise ValueError(f"personas: need exactly one per seat {SEATS}, got {seats}")
        _unique([p.name for p in self.personas], "personas")
        return self


class PersonaScores(BaseModel):
    model_config = STRICT
    desirability: int
    clarity: int
    feasibility: int

    @model_validator(mode="after")
    def _bounds(self) -> "PersonaScores":
        for k in ("desirability", "clarity", "feasibility"):
            v = getattr(self, k)
            if not 0 <= v <= 5:
                raise ValueError(f"{k}: {v} outside 0..5")
        return self


class MeanScores(BaseModel):
    model_config = STRICT
    desirability: float
    clarity: float
    feasibility: float


class PersonaReactionDraft(BaseModel):
    model_config = STRICT
    scores: PersonaScores
    objections: list[str]
    one_change: str

    @field_validator("objections")
    @classmethod
    def _obj(cls, v):
        return _unique(_count(v, "objections", 2, 6), "objections")


class PersonaReaction(PersonaReactionDraft):
    persona: Seat


class ReactionReport(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["panel"] = "panel"
    run_id: str
    parent: str
    cast: list[PersonaSpec]
    reactions: list[PersonaReaction]
    means: MeanScores
    kill: bool
    kill_reasons: list[str]

    @field_validator("cast")
    @classmethod
    def _cast(cls, v):
        seats = [p.seat for p in v]
        if sorted(seats) != sorted(SEATS):
            raise ValueError(f"cast: need exactly one persona per seat, got {seats}")
        return v

    @field_validator("reactions")
    @classmethod
    def _full_samples(cls, v):
        seats = [r.persona for r in v]
        counts = {s: seats.count(s) for s in SEATS}
        one = all(c == 1 for c in counts.values()) and len(v) == len(SEATS)
        two = all(c == 2 for c in counts.values()) and len(v) == 2 * len(SEATS)
        if not (one or two):
            raise ValueError(f"reactions: need one (or two) per seat, got {seats}")
        return v


# ---------------------------------------------------------------- Design


class DesignScreen(BaseModel):
    model_config = STRICT
    name: str
    layout_description: str
    components_used: list[str]
    maps_behaviors: list[int]
    covers_screen_ids: list[str] = []

    @field_validator("layout_description")
    @classmethod
    def _layout(cls, v: str) -> str:
        return check_vague(v, "layout_description")

    @field_validator("components_used")
    @classmethod
    def _comp(cls, v):
        return _unique(_count(v, "components_used", 1, 8), "components_used")


class DesignSpecDraft(BaseModel):
    model_config = STRICT
    screens: list[DesignScreen]

    @field_validator("screens")
    @classmethod
    def _screens(cls, v):
        _count(v, "screens", 1, 4)
        _unique([sc.name for sc in v], "screens")
        return v


class DesignSpec(DesignSpecDraft):
    schema_version: Literal["1"] = "1"
    stage: Literal["design"] = "design"
    run_id: str
    parent: str



# ------------------------------------------------------------- Interview (Discovery)


class InterviewQuestion(BaseModel):
    model_config = STRICT
    id: str
    question: str
    why: str

    @field_validator("question")
    @classmethod
    def _q(cls, v: str) -> str:
        if len(v) < 10:
            raise ValueError("question: shorter than 10 chars")
        return v


class InterviewQuestionsDraft(BaseModel):
    model_config = STRICT
    questions: list[InterviewQuestion]

    @field_validator("questions")
    @classmethod
    def _qs(cls, v):
        _count(v, "questions", 3, 8)
        _unique([q.id for q in v], "questions")
        return v


class IdeaRevisionDraft(BaseModel):
    """A proposed new idea file. Never written without the human approving the diff."""
    model_config = STRICT
    prose: str
    musts: list[str]
    nevers: list[str]
    change_note: str

    @field_validator("prose")
    @classmethod
    def _prose(cls, v: str) -> str:
        if len(v) < 50:
            raise ValueError("prose: shorter than 50 chars")
        return v

    @field_validator("musts")
    @classmethod
    def _musts(cls, v):
        return _unique(_count(v, "musts", 1, 10), "musts")

    @field_validator("nevers")
    @classmethod
    def _nevers(cls, v):
        return _unique(_count(v, "nevers", 0, 10), "nevers")

    @field_validator("change_note")
    @classmethod
    def _note(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("change_note: empty")
        return v


# ---------------------------------------------------------------- TechSpec (Architect)


class Entity(BaseModel):
    model_config = STRICT
    name: str
    fields: list[str]

    @field_validator("fields")
    @classmethod
    def _fields(cls, v):
        return _unique(_count(v, "entity.fields", 1, 12), "entity.fields")


def _app_file(v: str, field: str) -> str:
    if not v.startswith(ALLOWED_DIRS):
        raise ValueError(f"{field}: '{v}' must be under app/, lib/ or tests/")
    return v


class Interface(BaseModel):
    """One FE<->BE contract: the file that implements it and the file that consumes it."""
    model_config = STRICT
    name: str
    backend_file: str
    frontend_file: str
    shape: list[str]

    @field_validator("backend_file", "frontend_file")
    @classmethod
    def _files(cls, v: str, info) -> str:
        return _app_file(v, info.field_name)

    @field_validator("shape")
    @classmethod
    def _shape(cls, v):
        return _count(v, "interface.shape", 1, 8)


class TechSpecDraft(BaseModel):
    model_config = STRICT
    entities: list[Entity]
    interfaces: list[Interface]

    @field_validator("entities")
    @classmethod
    def _entities(cls, v):
        _count(v, "entities", 1, 8)
        _unique([e.name for e in v], "entities")
        return v

    @field_validator("interfaces")
    @classmethod
    def _interfaces(cls, v):
        _count(v, "interfaces", 1, 8)
        _unique([i.name for i in v], "interfaces")
        return v


class TechSpec(TechSpecDraft):
    schema_version: Literal["1"] = "1"
    stage: Literal["architect"] = "architect"
    run_id: str
    parent: str


# ---------------------------------------------------------------- UXFlows (UX Designer)


class UxScreen(BaseModel):
    model_config = STRICT
    id: str
    name: str
    purpose: str

    @field_validator("purpose")
    @classmethod
    def _purpose(cls, v: str) -> str:
        return check_vague(v, "screen.purpose")


class FlowStep(BaseModel):
    model_config = STRICT
    screen_id: str
    action: str


class UxFlow(BaseModel):
    model_config = STRICT
    name: str
    covers_behaviors: list[int]
    steps: list[FlowStep]

    @field_validator("steps")
    @classmethod
    def _steps(cls, v):
        return _count(v, "flow.steps", 1, 10)


class UXFlowsDraft(BaseModel):
    model_config = STRICT
    screens: list[UxScreen]
    flows: list[UxFlow]

    @field_validator("screens")
    @classmethod
    def _screens(cls, v):
        _count(v, "screens", 1, 4)
        _unique([sc.id for sc in v], "screens")
        return v

    @field_validator("flows")
    @classmethod
    def _flows(cls, v):
        _count(v, "flows", 1, 8)
        _unique([f.name for f in v], "flows")
        return v


class UXFlows(UXFlowsDraft):
    schema_version: Literal["1"] = "1"
    stage: Literal["ux"] = "ux"
    run_id: str
    parent: str


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
    stage: Literal["build", "repair"] = "build"
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
                         builder: str = "claude_code", stage: str = "build") -> "BuildResult":
        u = raw.get("usage") or {}
        cost = float(raw.get("total_cost_usd") or 0.0)
        return cls(
            stage=stage,
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


# ---------------------------------------------------------------- Split build

Role = Literal["backend", "frontend"]


class SplitBuildResult(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["build"] = "build"
    run_id: str
    parent: str
    app_dir: str
    parts: list[BuildResult]
    roles: list[Role]
    files_written: list[str]
    overlap: list[str]

    @model_validator(mode="after")
    def _two(self) -> "SplitBuildResult":
        if len(self.parts) != 2 or self.roles != ["backend", "frontend"]:
            raise ValueError("parts: exactly two, roles [backend, frontend]")
        return self


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
    asset_refs_total: int = 0
    asset_refs_missing: list[str] = []
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
            and not self.asset_refs_missing
        )
        object.__setattr__(self, "verify_pass", ok)
        return self


# ---------------------------------------------------------------- Ship


class ShipRecord(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["ship"] = "ship"
    run_id: str
    parent: str
    url: str
    deployed_at: str
    vercel_output_tail: str

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError(f"url: '{v}' must be https")
        return v



# ------------------------------------------------- Review / Security / Analytics (programs)

ReviewFindingKind = Literal[
    "locked_file", "template_file_deleted", "planned_file_missing", "scope_overlap", "stray_file",
]


class ReviewFinding(BaseModel):
    model_config = STRICT
    kind: ReviewFindingKind
    path: str
    detail: str


class ReviewReport(BaseModel):
    """Crew station 10. Written by a program reading the tree, never by a model."""
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["review"] = "review"
    run_id: str
    parent: str
    findings: list[ReviewFinding]
    review_pass: bool = False

    @model_validator(mode="after")
    def _compute_pass(self) -> "ReviewReport":
        object.__setattr__(self, "review_pass", not self.findings)
        return self


SecurityFindingKind = Literal["secret", "dangerous_api", "dependency_added", "audit_vulnerability"]


class SecurityFinding(BaseModel):
    model_config = STRICT
    kind: SecurityFindingKind
    path: str
    detail: str


class SecurityReport(BaseModel):
    """Crew station 12. A program: secret scan, dangerous APIs, supply chain, npm audit."""
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["security"] = "security"
    run_id: str
    parent: str
    findings: list[SecurityFinding]
    audit_ran: bool
    notes: list[str] = []
    security_pass: bool = False

    @model_validator(mode="after")
    def _compute_pass(self) -> "SecurityReport":
        object.__setattr__(self, "security_pass", not self.findings)
        return self


class UrlCheck(BaseModel):
    model_config = STRICT
    url: str
    status: int
    response_ms: int
    bytes: int


class AnalyticsReport(BaseModel):
    """Crew station 14. Watches what actually happened out there: the live URL, measured."""
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    stage: Literal["analytics"] = "analytics"
    run_id: str
    parent: str
    url: str
    checks: list[UrlCheck]
    analytics_pass: bool = False

    @model_validator(mode="after")
    def _compute_pass(self) -> "AnalyticsReport":
        object.__setattr__(self, "analytics_pass",
                           bool(self.checks) and all(200 <= c.status < 300 for c in self.checks))
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


RunStatus = Literal["running", "success", "verify_failed", "failed", "aborted", "killed",
                    "verified_unshipped"]


class RunManifest(BaseModel):
    model_config = STRICT
    schema_version: Literal["1"] = "1"
    run_id: str
    graph: str
    idea_id: str
    started_at: str
    finished_at: str | None
    status: RunStatus
    failed_stage: str | None
    stages: list[StageRecord]
    variant_stages: list[str] = []
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
