---
name: playwright-testing
description: Standards for writing and reviewing Playwright tests (API and UI) in TypeScript. Fixtures and a data factory instead of in-file helpers, setup through the API, page objects, persona fixtures instead of credentials, web-first assertions, no conditional assertions, status checks with the response body. Use when writing, generating, reviewing, or refactoring Playwright tests, or when a test-quality gap is found that should become a rule.
---

# playwright-testing

Write Playwright tests that stay readable, fail for one reason, and don't repeat the same setup in every file. Each rule has an ID (PW-n) that a repo's `AGENTS.md` and lint messages use too.

## 1. Before writing a test

1. Read the repo's test `AGENTS.md` (or `CLAUDE.md`). It lists the fixtures, factory methods, and page objects that already exist. Use them. The repo's file wins where it is more specific than this one.
2. Read the fixtures module the specs import from, and one existing spec of the same kind (API or UI).
3. If the repo has no fixtures module yet, create one before the second test that needs shared setup (PW-1).

## 2. Rules

Rules marked **lint** have a detector in [`eslint.config.mjs`](eslint.config.mjs), next to this file; copy it into a repo to enforce them.

**PW-1. Spec files hold tests only** (lint). Shared steps live in fixtures: a data factory for setup, page objects for UI. A helper function in a spec gets copied into the next spec, and they drift.

```ts
// Bad: every spec re-implements "vendor in review"
async function vendorInReview(api) { /* 40 lines */ }
// Good
const { extractions } = await factory.vendorInReview();
```

**PW-2. Arrange through the API.** Build state with the factory, then drive the UI only for the steps under test. UI-driven setup is slow, flaky, and fails for reasons unrelated to the test.

**PW-3. UI goes through page objects.** Locators and multi-step actions live in a page object, served as a fixture. A label change should touch one file, not every spec.

**PW-4. No credentials in specs** (lint). Sign in through persona fixtures (`apiAs`, `signIn`, `pageAs`). A second role in a UI test gets its own signed-in page from a fixture, never a hand-rolled login.

```ts
// Bad
await page.request.post("/api/auth/login", { data: { email: "reviewer@acme.test", password: "pw" } });
// Good
const reviewer = await pageAs("reviewer");
```

**PW-5. No fixed waits** (lint). Use web-first assertions, which retry, and `expect.poll` for background work. Never `waitForTimeout`, `networkidle`, or `{ force: true }`.

**PW-6. One import** (lint). Specs import tests, fixtures, helpers, and types from the fixtures module, not from `@playwright/test` or inline `import("...")` types.

**PW-7. Role, label, and text locators only** (lint). No CSS or XPath. When an element can't be found by role or label, that's an accessibility bug to fix in the app.

**PW-8. Every assertion runs on every path** (lint). No `if` around `expect`. For table-driven tests, split rows by expected outcome into separate tests.

```ts
// Bad
for (const [file, status] of rows) { ... if (status === 415) expect(body.error.code).toBe("unsupported_type"); }
// Good: one loop for accepted rows, one test (or loop) for refused rows, each asserting unconditionally
```

**PW-9. Status assertions carry the body** (lint). Use an `expectStatus(response, code)` helper that attaches the body to the failure and returns the parsed JSON. "Expected 201, received 422" alone sends the reader, or an agent, guessing.

**PW-10. One behavior per test, Arrange-Act-Assert.** Mark each step with a comment. A test generated from a story carries the story's tag: `{ tag: "@KEY-12" }`.

**PW-11. Tests don't share data.** Each test creates what it needs and uses unique names for anything with a uniqueness constraint. Assume only seeded personas exist. Tests then run in parallel and in any order.

**PW-12. Assert the stored outcome.** When a story says something is saved, re-read it. A response can echo a value that was never committed.

## 3. Checklist before handing over a test

- [ ] It uses existing fixtures, factory methods, and page objects; anything missing was added there, not in the spec.
- [ ] `lint` and `typecheck` pass.
- [ ] It passes against the running app.
- [ ] It fails when the behavior it covers is broken. Break the rule in the app once (or reason it through) and confirm.

## 4. When you find a gap

A gap is a test-quality problem these rules don't yet prevent. Handle it twice:

1. **Fix it** in the repo's tests.
2. **Make it a rule.** Add or sharpen a PW rule here and in the repo's `AGENTS.md`. Cite where it was found, give a bad and a good example, and add a detector to `eslint.config.mjs` when one is possible: a rule that lint enforces doesn't regress.

Both go out as pull requests for a person to review. Never merge them automatically.

## Provenance

Most rules come from gaps found in an audit of [vendor-intake-app](https://github.com/dholman7/vendor-intake-app)'s e2e suite (2026-11-01): duplicated setup in six specs (PW-1), UI tests building their data through the UI (PW-2), no page objects (PW-3), hardcoded credentials (PW-4), an inline type import (PW-6), conditional assertions (PW-8), and bare status checks with unchecked setup calls (PW-9), with no lint to catch any of them. PW-12 comes from a review-loop finding on that repo's KAN-4 test. PW-5, PW-7, PW-10, and PW-11 record practices the suite already followed, so they stay followed.
