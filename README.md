# pipeline

Idea in, product out. The first decision number for an agent pipeline: does a staged pipeline (v1) beat one strong agent (v0) on the same ideas?

Two LangGraph graphs share typed contracts and deterministic evaluators. No model grades a model. The only end-to-end judgement is a human on a rubric written before the run.

- v0: Intake -> Build -> Verify
- v1: Intake -> Plan -> Build -> Verify

Intake and Plan are one structured call each (`claude -p --json-schema --tools ""`). Build is Claude Code headless, confined to the app directory, capped in turns and wall-clock. Verify runs vitest, eslint, `next build` and tsc. Every artifact is an immutable JSON file under `runs/<run_id>/` with a hash chain back to the idea file.

**Keep this repo outside iCloud.** `~/Desktop` and `~/Documents` are synced on this Mac, and iCloud evicted the venv and fought the cloned `node_modules` until nothing could import. `~/code/pipeline` is the home.

## Setup

    /opt/homebrew/bin/python3.13 -m venv .venv
    .venv/bin/pip install -e ".[dev]"
    .venv/bin/python -m pipeline template-check     # npm ci in templates/next-app once

Claude Code must be logged in (subscription). `ANTHROPIC_API_KEY` is stripped from every call on purpose.

## Run

    .venv/bin/python -m pipeline run --graph v1 --idea 01            # pauses before Build
    .venv/bin/python -m pipeline run --graph v0 --idea 01 --yes      # no pause
    .venv/bin/python -m pipeline run --graph v1 --idea corpus/dev/url-word-counter.md --yes   # a dev idea
    .venv/bin/python -m pipeline run --graph crew --idea corpus/dev/url-word-counter.md --yes   # the whole diagram; still pauses at publish
    .venv/bin/python -m pipeline run --graph v1 --idea 01 --yes --mock   # zero tokens, fixture app
    .venv/bin/python -m pipeline eval --graph v0 --yes               # whole corpus, appends eval/results/v0.jsonl
    .venv/bin/python -m pipeline report                              # the table, joined with eval/scores.csv
    .venv/bin/python -m pipeline verify-only --run <run_id>          # re-run Verify without paying for Build

Progress lines go to stderr, one per stage. To watch a run: `tail -f runs/live.log` when started as

    nohup .venv/bin/python -m pipeline run --graph v1 --idea 01 --yes > runs/live.log 2>&1 &

and `ls apps/<run_id>/app apps/<run_id>/tests` to see files appear while Build works.

## Tests

    .venv/bin/pytest            # unit, no toolchain, no tokens
    .venv/bin/pytest -m slow    # real vitest/eslint/next/tsc on the fixture app, no tokens

## Layout

- `pipeline.toml` every cap and model, nothing else holds one
- `pipeline/contracts.py` Brief, Plan, BuildResult, TestReport, StageFailure, RunManifest
- `pipeline/evaluators.py` deterministic per-stage gates
- `pipeline/graph.py` the thin supervisor
- `corpus/ideas/` the frozen corpus, `corpus/dev/` ideas for development runs only
- `eval/rubric.md`, `eval/promotion_rule.md` written before the run, tagged `eval-frozen`
- `runs/`, `apps/` per-run artifacts and generated apps, gitignored
