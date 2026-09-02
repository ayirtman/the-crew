"""Stage 1. The only stage that brings information from outside the model, and the only one
allowed to touch the network. One structured call with WebSearch enabled."""
from __future__ import annotations

import urllib.request
from pathlib import Path

from pipeline.config import Config
from pipeline.contracts import Brief, EvidencePack, EvidencePackDraft
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def default_fetch(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        return urllib.request.urlopen(req, timeout=6).status
    except urllib.error.HTTPError as e:
        return e.code


def search_count(raw: dict) -> int:
    return sum(int(m.get("webSearchRequests") or 0) for m in (raw.get("modelUsage") or {}).values())


def produce(*, brief: Brief, parent_sha: str, caller: StructuredCaller, cfg: Config,
            fetch=default_fetch) -> tuple[EvidencePack, CallMeta]:
    from pipeline import evaluators

    stage = cfg.stages["evidence"]
    user = (
        f"PRODUCT IDEA:\ntitle: {brief.title}\nproblem: {brief.problem}\n"
        f"target user: {brief.target_user}\nfeature: {brief.single_feature}\n\n"
        "Research this. Find evidence and competitors with real, opened sources."
    )
    def check(draft: EvidencePackDraft) -> list[str]:
        # the true search count only exists in the raw result after the call, so the in-stage
        # retry checks everything else (a placeholder of 1) and the graph-level evaluator
        # enforces the real count recorded on the artifact.
        pack = EvidencePack(run_id=brief.run_id, parent=parent_sha, web_search_requests=1,
                            **draft.model_dump())
        return evaluators.evaluate_evidence(pack, cfg.evidence, fetch=fetch)

    res = call_with_retry(caller, system_file=PROMPTS / "evidence_system.md", user=user,
                          schema=EvidencePackDraft, stage=stage, attempts=stage.max_attempts,
                          check=check)
    draft: EvidencePackDraft = res.parsed  # type: ignore[assignment]
    pack = EvidencePack(run_id=brief.run_id, parent=parent_sha,
                        web_search_requests=search_count(res.raw), **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)
    return pack, meta
