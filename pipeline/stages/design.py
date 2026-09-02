"""Stage 4. New signal if and only if grounded: the DesignSpec may only use components from the
corpus that ships in the template. One structured call, no tools."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.config import Config
from pipeline.contracts import Brief, DesignSpec, DesignSpecDraft, EvidencePack, UXFlows
from pipeline.llm import StructuredCaller, call_with_retry
from pipeline.stages import CallMeta

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def load_components(template_dir: Path) -> list[str]:
    return json.loads((Path(template_dir) / "design" / "components.json").read_text())["components"]


def produce(*, brief: Brief, evidence: EvidencePack | None, parent_sha: str, components: list[str],
            caller: StructuredCaller, cfg: Config, ux: UXFlows | None = None) -> tuple[DesignSpec, CallMeta]:
    from pipeline import evaluators

    stage = cfg.stages["design"]
    brief_json = brief.model_dump_json(indent=2, exclude={"run_id", "parent", "stage", "schema_version"})
    ev = ""
    if evidence is not None:
        ev = "\n\nEVIDENCE:\n" + evidence.model_dump_json(indent=2, include={"claims"})
    uxs = ""
    if ux is not None:
        uxs = ("\n\nUX FLOWS (cover every screen id via covers_screen_ids):\n"
               + ux.model_dump_json(indent=2, include={"screens", "flows"}))
    user = (f"BRIEF:\n{brief_json}{ev}{uxs}\n\nCORPUS COMPONENTS (the only ones you may use):\n"
            + "\n".join(f"- {c}" for c in components)
            + "\n\nProduce the DesignSpec. must_have_behaviors are indexed from 0; every index must map to a screen.")

    def check(draft: DesignSpecDraft) -> list[str]:
        return evaluators.evaluate_design(
            DesignSpec(run_id=brief.run_id, parent=parent_sha, **draft.model_dump()), brief, components, ux=ux)

    res = call_with_retry(caller, system_file=PROMPTS / "design_system.md", user=user,
                          schema=DesignSpecDraft, stage=stage, attempts=stage.max_attempts, check=check)
    draft: DesignSpecDraft = res.parsed  # type: ignore[assignment]
    spec = DesignSpec(run_id=brief.run_id, parent=parent_sha, **draft.model_dump())
    meta = CallMeta(model=stage.model or "haiku", usage=res.usage, cost_reported=res.cost_reported,
                    wall_ms=res.duration_ms, num_turns=res.num_turns, attempts=res.attempts)
    return spec, meta
