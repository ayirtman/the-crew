import json
import subprocess
from pathlib import Path

from pipeline import evaluators
from pipeline.config import load_config
from pipeline.contracts import Brief, Plan, SplitBuildResult
from pipeline.stages import build_split

FIX = Path(__file__).parent / "fixtures"
CFG = load_config("pipeline.toml")


def _brief():
    d = json.loads((FIX / "brief_good.json").read_text())
    return Brief(run_id="r1", idea_id="01", parent="sha256:idea", **d)


def _plan():
    d = json.loads((FIX / "plan_good.json").read_text())
    return Plan(run_id="r1", parent="sha256:brief", constraints=[], **d)


class FakeProc:
    def __init__(self, stdout, cwd, writes):
        self._stdout, self._cwd, self._writes = stdout, Path(cwd), writes
        self.pid = 4242
        self.returncode = 0

    def communicate(self, timeout=None):
        for rel in self._writes:
            p = self._cwd / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("// built")
        return self._stdout, ""

    def kill(self):
        pass


class FakePopen:
    def __init__(self, writes_by_role):
        self.writes_by_role = writes_by_role
        self.launches = []

    def __call__(self, argv, *, cwd, env, **kw):
        prompt = argv[2]
        role = "backend" if "app/api/" in prompt and "You own ONLY" in prompt.split("frontend")[0] else "frontend"
        role = "backend" if "tests/api" in prompt else "frontend"
        self.launches.append({"argv": argv, "role": role, "kw": kw})
        return FakeProc((FIX / "claude_result_success.json").read_text(), cwd, self.writes_by_role[role])


def _app(tmp_path):
    app = tmp_path / "app"
    (app / "app").mkdir(parents=True)
    (app / "package.json").write_text("{}")
    return app


def _produce(tmp_path, popen):
    return build_split.produce(app_dir=_app(tmp_path), run_dir=tmp_path / "run", brief=_brief(),
                               plan=_plan(), design=None, parent_sha="s", cfg=CFG, popen=popen,
                               artifact_prefix="06-build")


def test_both_builders_launch_before_either_wait_and_have_scopes(tmp_path):
    fp = FakePopen({"backend": ["app/api/count/route.ts", "lib/count.ts", "tests/api/count.test.ts"],
                    "frontend": ["app/page.tsx", "tests/ui/page.test.tsx"]})
    res, meta = _produce(tmp_path, fp)
    assert len(fp.launches) == 2
    prompts = [l["argv"][2] for l in fp.launches]
    assert any("app/api/**" in p for p in prompts) and any("app/page.tsx" in p for p in prompts)
    assert all(l["kw"].get("start_new_session") for l in fp.launches)
    assert res.roles == ["backend", "frontend"] and res.overlap == []
    assert "app/page.tsx" in res.files_written and "lib/count.ts" in res.files_written
    assert (tmp_path / "run" / "06-build.backend.raw.json").exists()
    assert (tmp_path / "run" / "06-build.frontend.raw.json").exists()


def test_scopes_are_disjoint_by_construction():
    # a shared tree diff cannot attribute a file both builders could claim, so the scopes
    # themselves must never overlap; this pins that invariant.
    for b in build_split.SCOPES["backend"]:
        for f in build_split.SCOPES["frontend"]:
            assert not b.startswith(f) and not f.startswith(b), (b, f)


def test_out_of_scope_write_is_rejected(tmp_path):
    fp = FakePopen({"backend": ["lib/count.ts", "vitest.helper.ts"], "frontend": ["app/page.tsx"]})
    res, _ = _produce(tmp_path, fp)
    reasons = evaluators.evaluate_split_build(res, None)
    assert any("vitest.helper.ts" in r for r in reasons)


def test_split_result_contract_requires_two_parts(tmp_path):
    import pytest
    from pydantic import ValidationError
    fp = FakePopen({"backend": ["lib/a.ts"], "frontend": ["app/page.tsx"]})
    res, _ = _produce(tmp_path, fp)
    with pytest.raises(ValidationError):
        SplitBuildResult(**{**res.model_dump(), "parts": res.parts[:1]})
