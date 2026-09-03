from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pipeline", description="Idea in, product out. v0 vs v1.")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=".", help="project root holding pipeline.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run one idea through one graph", parents=[common])
    from pipeline.variants import VARIANTS
    r.add_argument("--graph", choices=sorted(VARIANTS), required=True)
    r.add_argument("--idea", required=True, help="idea id (01) from corpus/ideas, or a path to a dev idea")
    r.add_argument("--yes", action="store_true", help="skip the pause before Build (the publish pause always stays)")
    r.add_argument("--mock", action="store_true", help="no tokens: canned Brief/Plan, fixture app")

    d = sub.add_parser("develop", help="interview first, then run; a panel kill re-interviews (max 2)",
                       parents=[common])
    d.add_argument("--graph", choices=sorted(VARIANTS), default="crew")
    d.add_argument("--idea", required=True)
    d.add_argument("--yes", action="store_true", help="skip the pause before Build (the publish pause always stays)")
    d.add_argument("--mock", action="store_true")

    e = sub.add_parser("eval", help="run the whole corpus through one graph", parents=[common])
    e.add_argument("--graph", choices=sorted(VARIANTS), required=True)
    e.add_argument("--yes", action="store_true")
    e.add_argument("--mock", action="store_true")
    e.add_argument("--force", action="store_true", help="re-run ideas that already have a result")

    sub.add_parser("report", help="print the v0 vs v1 table", parents=[common])

    v = sub.add_parser("verify-only", help="re-run Verify on an existing run's app", parents=[common])
    v.add_argument("--run", required=True)

    sh = sub.add_parser("ship", help="deploy a verify-passing run's app to Vercel (manual, never automatic)",
                        parents=[common])
    sh.add_argument("--run", required=True)

    w = sub.add_parser("watch", help="live dashboard for a run in the browser", parents=[common])
    w.add_argument("--run", default=None, help="run id (default: newest under runs/)")
    w.add_argument("--port", type=int, default=8787)

    sub.add_parser("template-check", help="npm ci the template if needed", parents=[common])
    return p


def main(argv: list[str] | None = None) -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stderr, force=True)
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    if args.cmd == "run":
        from pipeline.runner import run_one
        out = run_one(root=root, graph=args.graph, idea_id=args.idea, yes=args.yes, mock=args.mock)
        t = out.manifest.totals
        print(f"{out.status}  {out.run_dir}  cost ${t.cost_usd:.4f} (billed ${t.billed_usd:.4f})  "
              f"{t.wall_ms / 1000:.1f}s")
        return 0 if out.status in ("success", "verify_failed", "verified_unshipped") else 1
    if args.cmd == "develop":
        from pipeline.runner import develop
        out = develop(root=root, graph=args.graph, idea_id=args.idea, yes=args.yes, mock=args.mock)
        if out is None:
            print("nothing run")
            return 1
        t = out.manifest.totals
        print(f"{out.status}  {out.run_dir}  cost ${t.cost_usd:.4f} (billed ${t.billed_usd:.4f})")
        return 0 if out.status in ("success", "verify_failed", "verified_unshipped", "killed") else 1
    if args.cmd == "eval":
        from pipeline.eval import run_corpus
        return run_corpus(root=root, graph=args.graph, yes=args.yes, mock=args.mock, force=args.force)
    if args.cmd == "report":
        from pipeline.report import render
        print(render(root))
        return 0
    if args.cmd == "verify-only":
        from pipeline.runner import verify_only
        return verify_only(root=root, run_id=args.run)
    if args.cmd == "ship":
        from pipeline.ship import ship
        return ship(root=root, run_id=args.run)
    if args.cmd == "watch":
        import functools
        import http.server
        import webbrowser
        run_id = args.run
        if run_id is None:
            runs = sorted((root / "runs").glob("*/00-manifest.json"), key=lambda p: p.stat().st_mtime)
            if not runs:
                print("no runs yet")
                return 1
            run_id = runs[-1].parent.name
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
        url = f"http://localhost:{args.port}/dashboard/?run={run_id}"
        print(f"watching {run_id} at {url}  (Ctrl+C to stop)")
        webbrowser.open(url)
        with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as srv:
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                pass
        return 0
    if args.cmd == "template-check":
        from pipeline.stages.template import ensure_node_modules
        ensure_node_modules(root / "templates" / "next-app")
        print("template ok")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
