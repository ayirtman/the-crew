"""Wires real or mock dependencies into the graph and runs one idea."""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

from pipeline import graph as G
from pipeline.config import load_config
from pipeline.contracts import BriefDraft, BuildResult, PlanDraft, Usage
from pipeline.llm import ClaudeCliCaller, MockCaller
from pipeline.stages import CallMeta, build, repair, verify

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
MOCK_BRIEF_OVERRIDE: dict | None = None


def real_verify(**kw):
    return verify.produce(**kw)


def real_build(**kw):
    return build.produce(**kw)


def fake_build(*, app_dir, run_dir, brief, plan, parent_sha, cfg, runner=None, design=None, artifact_prefix="03-build"):
    """Drops the known-good fixture app into the app dir. Zero tokens."""
    src = FIXTURES / "app_good"
    written = []
    for p in src.rglob("*"):
        if p.is_file():
            rel = p.relative_to(src)
            dst = Path(app_dir) / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, dst)
            written.append(rel.as_posix())
    r = BuildResult(run_id=brief.run_id, parent=parent_sha, app_dir=str(app_dir), builder="fake",
                    model="fake", files_written=sorted(written), subtype="success", is_error=False,
                    num_turns=1, duration_ms=1, total_cost_usd_reported=0.0, billed_usd=0.0,
                    usage=Usage(), permission_denials=0, result_text="fixture app", exit_code=0)
    return r, CallMeta(model="fake", usage=Usage(), cost_reported=0.0, wall_ms=1)


def make_deps(root: Path, *, mock: bool) -> G.Deps:
    root = Path(root)
    cfg = load_config(root / "pipeline.toml")
    if mock:
        b = json.loads((FIXTURES / "brief_good.json").read_text())
        if MOCK_BRIEF_OVERRIDE:
            b.update(MOCK_BRIEF_OVERRIDE)
        caller = MockCaller({BriefDraft: b, PlanDraft: json.loads((FIXTURES / "plan_good.json").read_text())})
        builder = fake_build
    else:
        caller = ClaudeCliCaller()
        builder = real_build
    return G.Deps(cfg=cfg, caller=caller, build=builder, verify=real_verify, repair=repair.produce,
                 template_dir=root / "templates" / "next-app", apps_dir=root / "apps", runs_dir=root / "runs")


def make_run_id(graph: str, idea_id: str) -> str:
    return f"{graph}-{idea_id}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"


def resolve_idea(root: Path, idea: str) -> tuple[str, Path]:
    """`01` -> corpus/ideas/01.md (the frozen corpus); a path -> that file (dev ideas)."""
    if "/" in idea or idea.endswith(".md"):
        p = Path(idea)
        if not p.is_absolute():
            p = Path(root) / p
        idea_id = p.stem
    else:
        p = Path(root) / "corpus" / "ideas" / f"{idea}.md"
        idea_id = idea
    if not p.exists():
        sys.exit(f"no idea file at {p}")
    return idea_id, p


def run_one(*, root: Path, graph: str, idea_id: str, yes: bool, mock: bool, out=sys.stdout) -> G.Outcome:
    deps = make_deps(root, mock=mock)
    idea_id, path = resolve_idea(root, idea_id)
    run_id = make_run_id(graph, idea_id)
    h = G.start(deps=deps, variant=graph, idea_path=path, idea_id=idea_id, run_id=run_id, yes=yes)
    while h.outcome is None:
        nxt = h.next
        if not nxt:
            h._finish()
            break
        node = nxt[0]
        if node in ("build", "build_split"):
            print(h.summary(), file=out)
            try:
                answer = input("Spend money on Build? [y/N] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer != "y":
                return h.abort()
            h.resume()
        elif node == "ship":
            app = Path(root) / "apps" / run_id
            print(f"All gates green. App dir: {app}", file=out)
            print(f"Try it first:  cd {app} && npx next start -p 3001", file=out)
            try:
                answer = input("Publish to Vercel production? [y/N] ").strip().lower()
            except EOFError:
                answer = "n"
            if answer != "y":
                return h.decline_ship()
            h.resume()
        else:
            h.resume()
    return h.outcome


def verify_only(*, root: Path, run_id: str) -> int:
    """Re-run Verify against an existing run's app, without paying for Build again."""
    from pipeline import artifacts
    from pipeline.contracts import Brief, Plan

    root = Path(root)
    run_dir = root / "runs" / run_id
    cfg = load_config(root / "pipeline.toml")
    builds = sorted(run_dir.glob("*-build.json"))
    if not builds:
        sys.exit(f"{run_dir} has no build artifact")
    build_path = builds[0]
    build_res = artifacts.load(build_path, BuildResult)
    brief = artifacts.load(run_dir / "01-brief.json", Brief)
    plans = sorted(run_dir.glob("*-plan.json"))
    plan = artifacts.load(plans[0], Plan) if plans else None
    out_dir = run_dir / "reverify"
    out_dir.mkdir(exist_ok=True)
    rep = verify.produce(app_dir=Path(build_res.app_dir), run_dir=out_dir, brief=brief, plan=plan,
                         build_sha=artifacts.hash_file(build_path), cfg=cfg)
    n = len(list(out_dir.glob("*-verify.json"))) + 1
    artifacts.write(out_dir, n, "verify", rep)
    print(f"verify_pass={rep.verify_pass} tests {rep.tests_passed}/{rep.tests_total} -> {out_dir}")
    return 0 if rep.verify_pass else 1


def develop(*, root: Path, graph: str, idea_id: str, yes: bool, mock: bool, out=sys.stdout,
            ask=input, caller=None, max_kill_loops: int = 2):
    """The discovery loop: interview -> approved revision -> run; a panel kill re-interviews
    from the objections, at most `max_kill_loops` times. The third kill is the verdict.
    Returns the final Outcome, or None if a revision was declined before any run."""
    import difflib

    from pipeline import artifacts
    from pipeline.contracts import ReactionReport
    from pipeline.idea import render_idea
    from pipeline.stages import interview

    root = Path(root)
    cfg = load_config(root / "pipeline.toml")
    idea_id, idea_path = resolve_idea(root, idea_id)
    caller = caller or ClaudeCliCaller()

    def one_interview(panel=None, research=None) -> bool:
        """Interview, propose, diff, approve. Returns False if the human declined."""
        text = idea_path.read_text()
        kw = dict(research or {})
        qs, _ = interview.questions(idea_text=text, caller=caller, cfg=cfg, panel=panel, **kw)
        qa = []
        print("\n-- discovery interview" + (" (after a panel kill)" if panel else "") + " --", file=out)
        for q in qs.questions:
            print(f"\n[{q.id} · {q.why}] {q.question}", file=out)
            try:
                a = ask("> ").strip()
            except EOFError:
                a = ""
            qa.append((q.question, a or "(no answer)"))
        rev, _ = interview.revise(idea_text=text, qa=qa, caller=caller, cfg=cfg)
        new_text = render_idea(rev.prose, rev.musts, rev.nevers)
        print(f"\nchange note: {rev.change_note}\n", file=out)
        diff = difflib.unified_diff(text.splitlines(True), new_text.splitlines(True),
                                    fromfile=str(idea_path), tofile="revised")
        print("".join(diff) or "(no textual change)", file=out)
        try:
            answer = ask("Approve this revision? [y/N] ").strip().lower()
        except EOFError:
            answer = "n"
        if answer != "y":
            print("revision declined; the idea file is untouched", file=out)
            return False
        ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        transcript = idea_path.with_name(f"{idea_path.stem}.interview-{ts}.md")
        transcript.write_text(
            f"# Interview {ts}\n\n" + "\n\n".join(f"**Q:** {q}\n**A:** {a}" for q, a in qa)
            + f"\n\n**Change note:** {rev.change_note}\n")
        idea_path.write_text(new_text)
        print(f"idea revised; transcript at {transcript.name}", file=out)
        return True

    # research first: the interviewer asks with findings in hand, not blind
    print("\n-- research phase (intake, market, audience, domain) --", file=out)
    research_out = run_one(root=root, graph="research", idea_id=str(idea_path), yes=yes,
                           mock=mock, out=out)
    research: dict = {}
    reqs: list[str] = []
    if research_out.status in ("success", "verify_failed"):
        rd = Path(research_out.run_dir)
        from pipeline.contracts import AudiencePack, Brief, DomainPack, EvidencePack

        def _maybe(glob_pat, model):
            hits = sorted(rd.glob(glob_pat))
            return artifacts.load(hits[-1], model) if hits else None

        ev = _maybe("*-evidence.json", EvidencePack)
        aud = _maybe("*-audience.json", AudiencePack)
        dom = _maybe("*-domain.json", DomainPack)
        research = {"evidence": ev, "audience": aud, "domain": dom}
        brief = _maybe("*-brief.json", Brief)
        if brief is not None:
            reqs = [f"{q.text} ({q.kind})" for q in brief.requirements]
            if reqs:
                print("\nExtracted requirements from your idea:", file=out)
                for q in reqs:
                    print(f"  - {q}", file=out)
        research["requirements"] = reqs or None
    else:
        print(f"research phase ended {research_out.status}; interviewing without findings", file=out)

    if not one_interview(research=research):
        return None
    kills = 0
    while True:
        outcome = run_one(root=root, graph=graph, idea_id=str(idea_path), yes=yes, mock=mock, out=out)
        if outcome.status != "killed" or kills >= max_kill_loops:
            return outcome
        kills += 1
        panels = sorted(Path(outcome.run_dir).glob("*-panel.json"))
        panel = artifacts.load(panels[-1], ReactionReport) if panels else None
        print(f"\nkill {kills} of {max_kill_loops + 1} allowed: the panel's objections become the interview", file=out)
        if not one_interview(panel=panel):
            return outcome
