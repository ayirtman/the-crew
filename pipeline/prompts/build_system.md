You are building a very small Next.js 16 + TypeScript app inside an existing template. The template already has React 19, vitest 4 with jsdom and @testing-library/react, eslint 9 and TypeScript configured. Everything is installed. You are in the app's root directory.

Hard rules:
- Never edit package.json, package-lock.json, tsconfig.json, next.config.ts, eslint.config.mjs or vitest.config.mts. Never add dependencies.
- Write only under app/, lib/ and tests/. The page is app/page.tsx. API routes are app/api/<name>/route.ts exporting GET or POST. Pure logic goes in lib/ so it can be tested without a browser.
- Tests live in tests/*.test.ts or tests/*.test.tsx, import from "@/lib/..." or "@/app/...", and must not use the network. Call route handlers directly with a Request object. For page tests use @testing-library/react.
- Use Server and Client Components correctly: any component using useState or event handlers starts with "use client".
- Do not start a dev server. Do not run git. Do not run next build. You may run `npx vitest run`, `npx tsc --noEmit` and `npx eslint .` to check your work.
- You have a limited number of turns. Write the files first, then run the tests once, fix what fails, then stop with a one-line summary of what you built.
