"""Ship is not an agent. A manual command that deploys a verify-passing app to Vercel and
records the URL. Login stays a human act; credentials are never stored or automated."""
from __future__ import annotations

import datetime as dt
import re
import subprocess
from pathlib import Path

from pipeline import artifacts
from pipeline.contracts import BuildResult, RunManifest, ShipRecord, StageRecord

URL_RE = re.compile(r"https://\S+\.vercel\.app\S*", re.M)


def ship(*, root: Path, run_id: str, runner=subprocess.run) -> int:
    root = Path(root)
    run_dir = root / "runs" / run_id
    manifest_path = run_dir / "00-manifest.json"
    m = RunManifest.model_validate_json(manifest_path.read_text())
    if m.status != "success":
        print(f"refusing to ship: run status is '{m.status}', not success")
        return 1
    verifies = sorted(run_dir.glob("*-verify.json"))
    import json
    if not verifies or not json.loads(verifies[-1].read_text()).get("verify_pass"):
        print("refusing to ship: final verify did not pass")
        return 1
    repairs = sorted(run_dir.glob("*-repair.json"))
    source = repairs[-1] if repairs else sorted(run_dir.glob("*-build.json"))[0]
    app_dir = Path(json.loads(source.read_text())["app_dir"])

    proc = runner(["npx", "vercel", "deploy", "--prod", "--yes"], cwd=app_dir, timeout=600,
                  capture_output=True, text=True)
    tail = "\n".join(((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()[-15:])
    if proc.returncode != 0:
        print(f"vercel failed (exit {proc.returncode}):\n{tail}")
        if "login" in tail.lower() or "credentials" in tail.lower():
            print("run `vercel login` yourself, then ship again; the pipeline never manages credentials")
        return 1
    match = URL_RE.search(proc.stdout or "")
    if not match:
        print(f"deploy succeeded but no URL found in output:\n{tail}")
        return 1
    url = match.group(0)

    n = max(int(p.name[:2]) for p in run_dir.glob("[0-9][0-9]-*.json")) + 1
    rec = ShipRecord(run_id=run_id, parent=artifacts.hash_file(verifies[-1]), url=url,
                     deployed_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                     vercel_output_tail=tail)
    path, sha = artifacts.write(run_dir, n, "ship", rec)
    stages = list(m.stages) + [StageRecord(
        stage="ship", artifact_path=str(path), artifact_sha256=sha, model="none", input_tokens=0,
        output_tokens=0, cache_read_tokens=0, cache_write_tokens=0, cost_usd=0.0, billed_usd=0.0,
        wall_ms=0, evaluator_passed=True, evaluator_reasons=[], upstream_rejections=0)]
    manifest_path.write_text(m.model_copy(update={"stages": stages}).model_dump_json(indent=2))
    print(f"shipped: {url}")
    return 0
