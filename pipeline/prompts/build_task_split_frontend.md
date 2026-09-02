You are one of two builders working on this app RIGHT NOW, in the same directory, at the same time.

You own ONLY these paths and may not create or edit anything else: app/page.tsx, app/layout.tsx, app/globals.css, app/page.module.css, tests/ui/**. The other builder owns app/api/** and lib/** and is writing them now; do not create placeholder versions of their files. Call the api exactly as the Brief's api contract describes.

BRIEF:
{{BRIEF}}

PLAN:
{{PLAN}}

Build the page against the api contract, and UI tests under tests/ui/ (create the folder) with @testing-library/react. Acceptance criteria whose test_file falls in your scope are yours; use the exact test_name titles. Writing any file outside your scope fails the run. Run `npx vitest run tests/ui` once, fix, stop with one line.
