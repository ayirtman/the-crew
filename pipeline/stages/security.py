"""Crew station 12: the Security Reviewer. A program: secret patterns, dangerous APIs, supply
chain drift, npm audit. Scans only what changed against the template; the template is trusted
because a human shipped it. An unreachable registry is a note, never a block."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from pipeline.config import Config
from pipeline.contracts import SecurityFinding, SecurityReport
from pipeline.stages.template import tree_hashes

Runner = Callable[..., subprocess.CompletedProcess]

SCAN_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".mts", ".css", ".json", ".md")

SECRET_PATTERNS = (
    (re.compile(r'(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*["\'][A-Za-z0-9_\-]{12,}["\']'),
     "credential-shaped literal"),
    (re.compile(r'sk-[A-Za-z0-9\-]{16,}'), "provider key pattern (sk-...)"),
    (re.compile(r'AKIA[0-9A-Z]{16}'), "AWS access key pattern"),
)

DANGEROUS_PATTERNS = (
    (re.compile(r'\beval\s*\('), "eval()"),
    (re.compile(r'\bnew\s+Function\s*\('), "new Function()"),
    (re.compile(r'child_process'), "child_process"),
    (re.compile(r'dangerouslySetInnerHTML'), "dangerouslySetInnerHTML"),
    (re.compile(r'fetch\(\s*["\']http://'), "plaintext http fetch"),
)


def produce(*, app_dir: Path, template_dir: Path, cfg: Config, parent_sha: str, run_id: str,
            runner: Runner = subprocess.run) -> SecurityReport:
    stage = cfg.stages["security"]
    app_dir = Path(app_dir)
    tpl = tree_hashes(Path(template_dir))
    app = tree_hashes(app_dir)
    changed = sorted(f for f, h in app.items() if tpl.get(f) != h)
    findings: list[SecurityFinding] = []
    notes: list[str] = []

    for f in changed:
        if f == "package.json":
            findings.append(SecurityFinding(kind="dependency_added", path=f,
                                            detail="package.json differs from the template; the template is the supply chain"))
        if not f.endswith(SCAN_SUFFIXES):
            continue
        try:
            text = (app_dir / f).read_text(errors="ignore")
        except OSError:
            continue
        for rx, label in SECRET_PATTERNS:
            if rx.search(text):
                findings.append(SecurityFinding(kind="secret", path=f, detail=label))
                break
        for rx, label in DANGEROUS_PATTERNS:
            if rx.search(text):
                findings.append(SecurityFinding(kind="dangerous_api", path=f, detail=label))
                break

    audit_ran = False
    try:
        proc = runner(["npm", "audit", "--omit=dev", "--audit-level=high", "--json"],
                      cwd=app_dir, timeout=stage.max_seconds, capture_output=True, text=True)
        data = json.loads(proc.stdout or "{}")
        vulns = (data.get("metadata") or {}).get("vulnerabilities") or {}
        n = int(vulns.get("high") or 0) + int(vulns.get("critical") or 0)
        audit_ran = True
        if n > 0:
            findings.append(SecurityFinding(kind="audit_vulnerability", path="package-lock.json",
                                            detail=f"npm audit: {n} high or critical vulnerabilities"))
    except Exception as e:
        notes.append(f"npm audit did not run: {type(e).__name__}: {e}")

    return SecurityReport(run_id=run_id, parent=parent_sha, findings=findings,
                          audit_ran=audit_ran, notes=notes)
