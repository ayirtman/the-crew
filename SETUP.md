# Running the crew on your own machine

This is an idea-in, product-out machine: 16 stations (research, a cast focus group of seven, PM, architect, UX, UI, two parallel builder agents, three deterministic verifiers, a publish gate, live analytics). You bring one idea file; the machine interviews you, researches, argues, builds, verifies, and stops at a publish button.

## Prerequisites

- **A Claude Max or Pro subscription with Claude Code installed and logged in** — the stations run `claude -p` under your login. Check: `claude --version` works and `claude -p "say ok"` answers without asking for an API key. Every run is billed to *your* subscription ($0 extra; a run reports ~$2-4 of notional cost). Do NOT set ANTHROPIC_API_KEY — that would bill real money.
- Python 3.13, Node 20+.
- macOS or Linux. (Developed on macOS; on Linux the template copy falls back to a slower path automatically. First Linux run may surface portability bugs — report them, that's useful.)
- Optional, only for pressing publish: `npx vercel login` with your own Vercel account.

## Setup

    git clone <this repo> && cd <repo>
    python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m pipeline template-check     # installs the app template's node_modules (~2 min)

## First run

    # the full experience: research first, then the machine interviews YOU about your idea,
    # you approve its revision as a diff, then the whole crew runs (~25-35 min)
    caffeinate -i .venv/bin/python -m pipeline develop --idea corpus/dev/url-word-counter.md
    # (Linux: drop `caffeinate -i`)

    # watch it live: every station, every artifact, the panel's cast and objections
    .venv/bin/python -m pipeline watch      # opens http://localhost:8787/dashboard/

Write your own idea as a small markdown file: prose + optional `## Must` and `## Never` bullet lists. Those lists are a contract the machine is forbidden to drop.

## What to expect

- The focus group may **kill** your idea with reasons. That's the system working; `develop` turns the objections into interview questions (max 2 loops).
- A green run ends `verified_unshipped` with the app in `apps/<run-id>/` — test it with `cd apps/<run-id> && npx next start -p 3001`, then publish with `.venv/bin/python -m pipeline ship --run <run-id>` if you want it live on your Vercel.
- Runs are immutable audit trails in `runs/<run-id>/` — numbered JSON artifacts with a sha256 parent chain.

## Rules the machine lives by

Typed contract at every boundary; a deterministic evaluator gates every stage; no LLM ever grades an LLM (the reviewers are programs); research must cite live sources it actually searched for; the human touches exactly two things: the idea and the publish button.
