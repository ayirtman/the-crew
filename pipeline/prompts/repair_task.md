The app in this directory failed verification. Fix it in place so every listed failure passes.

FAILURES:
{{FAILURES}}

Files the original builder wrote:
{{FILES}}

Rules:
- Change as little as possible. Fix the app or the test that is honestly wrong, but never delete tests or weaken assertions just to make them pass.
- Never touch package.json or any config file. Never add dependencies.
- A referenced asset that does not exist must be replaced with a path that exists in /assets/manifest.json.
- Run `npx vitest run` and `npx tsc --noEmit` once after your changes, fix what remains, then stop with a one-line summary.
