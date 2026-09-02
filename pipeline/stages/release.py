"""Crew station 13: the Release Engineer. Deploys the verified app to Vercel production.
Login stays a human act; credentials are never stored or automated. The graph pauses before
this stage unconditionally: pressing publish is one of the diagram's two human touches."""
from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path
from typing import Callable

from pipeline.config import Config
from pipeline.contracts import ShipRecord
from pipeline.ship import URL_RE
from pipeline.stages.build import BuilderError

Runner = Callable[..., subprocess.CompletedProcess]


def produce(*, app_dir: Path, parent_sha: str, run_id: str, cfg: Config,
            runner: Runner = subprocess.run) -> ShipRecord:
    stage = cfg.stages["ship"]
    proc = runner(["npx", "vercel", "deploy", "--prod", "--yes"], cwd=Path(app_dir),
                  timeout=stage.max_seconds, capture_output=True, text=True)
    tail = "\n".join(((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()[-15:])
    if proc.returncode != 0:
        hint = " (run `vercel login` yourself; the pipeline never manages credentials)" \
            if "login" in tail.lower() or "credentials" in tail.lower() else ""
        raise BuilderError(f"vercel deploy failed (exit {proc.returncode}){hint}:\n{tail}")
    m = URL_RE.search(proc.stdout or "")
    if not m:
        raise BuilderError(f"deploy succeeded but no URL found in output:\n{tail}")
    return ShipRecord(run_id=run_id, parent=parent_sha, url=m.group(0),
                      deployed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      vercel_output_tail=tail)
