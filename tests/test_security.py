"""Crew station 12: the Security Reviewer is a program. Secrets, dangerous APIs, supply chain."""
import json
import subprocess
from pathlib import Path

from pipeline.config import load_config
from pipeline.stages import security

CFG = load_config("pipeline.toml")

CLEAN_AUDIT = json.dumps({"metadata": {"vulnerabilities": {"high": 0, "critical": 0}}})


class FakeAudit:
    def __init__(self, stdout=CLEAN_AUDIT, returncode=0, raises=None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def _tree(tmp_path, files):
    tpl = tmp_path / "tpl"
    (tpl / "app").mkdir(parents=True)
    (tpl / "package.json").write_text('{"dependencies": {"next": "16.0.0"}}')
    (tpl / "app" / "layout.tsx").write_text("layout")
    app = tmp_path / "app"
    import shutil
    shutil.copytree(tpl, app)
    for f, content in files.items():
        p = app / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tpl, app


def _produce(tpl, app, runner=None):
    return security.produce(app_dir=app, template_dir=tpl, cfg=CFG, parent_sha="sha:verify",
                            run_id="r1", runner=runner or FakeAudit())


def test_clean_app_passes(tmp_path):
    tpl, app = _tree(tmp_path, {"lib/count.ts": "export const count = (s: string) => s.split(/\\s+/).length"})
    rep = _produce(tpl, app)
    assert rep.security_pass is True and rep.audit_ran is True and rep.parent == "sha:verify"


def test_hardcoded_secret_is_a_finding(tmp_path):
    tpl, app = _tree(tmp_path, {"lib/api.ts": 'const apiKey = "sk-proj-abc123def456ghi789jkl"'})
    rep = _produce(tpl, app)
    assert any(f.kind == "secret" and f.path == "lib/api.ts" for f in rep.findings)
    assert rep.security_pass is False


def test_aws_key_pattern_is_a_finding(tmp_path):
    tpl, app = _tree(tmp_path, {"lib/x.ts": 'const k = "AKIAIOSFODNN7EXAMPLE"'})
    rep = _produce(tpl, app)
    assert any(f.kind == "secret" for f in rep.findings)


def test_eval_and_child_process_are_findings(tmp_path):
    tpl, app = _tree(tmp_path, {
        "lib/a.ts": 'eval("2+2")',
        "app/api/x/route.ts": 'import { execSync } from "child_process"',
    })
    rep = _produce(tpl, app)
    kinds = [(f.kind, f.path) for f in rep.findings]
    assert ("dangerous_api", "lib/a.ts") in kinds and ("dangerous_api", "app/api/x/route.ts") in kinds


def test_dangerously_set_inner_html_is_a_finding(tmp_path):
    tpl, app = _tree(tmp_path, {"app/page.tsx": "<div dangerouslySetInnerHTML={{__html: x}} />"})
    rep = _produce(tpl, app)
    assert any(f.kind == "dangerous_api" for f in rep.findings)


def test_template_files_are_not_scanned(tmp_path):
    # the template itself may legitimately contain scary-looking strings; only the diff is scanned
    tpl, app = _tree(tmp_path, {})
    (tpl / "app" / "layout.tsx").write_text('eval("x")')
    (app / "app" / "layout.tsx").write_text('eval("x")')
    rep = _produce(tpl, app)
    assert rep.security_pass is True


def test_changed_package_json_is_a_supply_chain_finding(tmp_path):
    tpl, app = _tree(tmp_path, {"package.json": '{"dependencies": {"next": "16.0.0", "left-pad": "1.0.0"}}'})
    rep = _produce(tpl, app)
    assert any(f.kind == "dependency_added" and f.path == "package.json" for f in rep.findings)


def test_audit_vulnerabilities_are_findings(tmp_path):
    tpl, app = _tree(tmp_path, {})
    out = json.dumps({"metadata": {"vulnerabilities": {"high": 2, "critical": 1}}})
    rep = _produce(tpl, app, runner=FakeAudit(stdout=out, returncode=1))
    assert any(f.kind == "audit_vulnerability" and "3" in f.detail for f in rep.findings)


def test_unreachable_audit_is_a_note_not_a_finding(tmp_path):
    # no network must not block a ship; the report says the audit did not run
    tpl, app = _tree(tmp_path, {})
    rep = _produce(tpl, app, runner=FakeAudit(raises=OSError("no npm")))
    assert rep.audit_ran is False and rep.security_pass is True and rep.notes
