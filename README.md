# agent-dev-skills

Claude Code skills for engineering work:

- **review-loop**: pull request review in which every comment is checked against the code before it reaches the author, and new commits get a re-review that re-checks earlier comments.
- **playwright-testing**: standards for Playwright tests, each rule with an ID and, where possible, a lint rule that enforces it. See [below](#playwright-testing).
- **test-audit**: finds where a repo's tests break those rules, or have a problem no rule covers yet, with every finding checked against the code. See [below](#test-audit).

## review-loop

```
review ──> check citations ──> verify ──> comment ──> (new commits) ──> re-review + re-check old comments
```

| Step | What happens |
|---|---|
| **Review** | One Claude call with the diff and the full changed files. Each finding carries a file, a line, a label, whether it blocks, and the exact code it's about. |
| **Check citations** | Deterministic. The file must be in the diff, the quoted code must exist (a wrong line number is corrected to where the quote actually is), and the line must be one GitHub accepts comments on. Findings that fail are dropped, with the reason recorded. |
| **Verify** | A second call re-derives each finding from numbered code and the diff, without trusting the finding's own reasoning, then confirms, demotes, retargets, or drops it. |
| **Comment** | [Conventional Comments](https://conventionalcomments.org), with `(blocking)` only for real defects and a `suggestion` block when the fix is exact. Reviews are posted as `COMMENT`, never `APPROVE` or `REQUEST_CHANGES`. |
| **Re-review** | Reviews only the commits since the last reviewed one (recorded in a hidden marker), re-checks each earlier comment against the current code, and resolves the threads whose problem is gone. **It only ever resolves threads it started**; threads people opened are never touched. |

### Results

Measured on 42 one-file pull requests to [vendor-intake-app](https://github.com/dholman7/vendor-intake-app): the 32 seeded bugs from [agent-eval-gate](https://github.com/dholman7/agent-eval-gate)'s benchmark, which it should catch, and 10 harmless changes (docstrings, comments, a constant extracted, a seed row added), which it should leave alone. PR titles are neutral, so nothing hints at the defect.

| Run | Bugs caught | False alarms | Minor comments per harmless change | Cost |
|---|---|---|---|---|
| Claude Opus 5.5, with verification | 91% (29/32, CI 76–97%) | 0/10 | 0.0 | $0.78 |
| Claude Haiku 5.5, with verification | 88% (28/32, CI 72–95%) | 0/10 | 0.1 | $0.023 |
| Claude Haiku 5.5, without verification | 88% (28/32, CI 72–95%) | 0/10 | 0.3 | $0.017 |

What this does and doesn't show:

- **Both models catch most seeded bugs.** Their intervals overlap almost entirely, so this set can't tell Opus and Haiku apart.
- **Verification cuts noise.** On harmless changes it removed most minor comments without costing a single catch.
- **The harmless set above is too easy to measure precision.** Even unverified reviews raised no false alarms on it, so the harder set below was added.
- **The misses are subtle:** a field quietly deleted from the extraction spec, a sort order flipped to descending (which can look intentional), and `<=` changed to `<` on a 30-day window.

**Harmless changes that look risky.** [`evals/review/clean-hard.yaml`](evals/review/clean-hard.yaml) holds 14 more harmless changes, each rewriting something a reviewer is trained to distrust while keeping behavior identical:

- a tenant filter split into chained `where` calls
- the login check folded into a single path
- `size > max_bytes` rewritten as `size >= max_bytes + 1`
- a content-type check rewritten with De Morgan's law
- `fullmatch` changed to `match` with an explicit `\Z`
- the review queue rebuilt from two stable sorts
- `digits[-4:]` changed to `digits[5:]` behind a length-9 check

Each entry names the trap it sets (`looks_like`). [`check_clean.py`](evals/review/check_clean.py) proves each change harmless: applied alone to a copy of the app, lint, strict types, all 68 backend tests, and the OpenAPI document must come out unchanged. It first applies a known-breaking control change, which must fail, so a green result can't come from the checks testing the wrong code.

| Run | False alarms (blocking) | Any `issue` raised | Comments per change | Cost |
|---|---|---|---|---|
| Claude Opus 5.5, with verification | 0/14 (CI 0–22%) | 0/14 | 0.14 | $0.16 |
| Claude Opus 5.5, without verification | 0/14 | 0/14 | 0.14 | $0.15 |
| Claude Haiku 5.5, with verification | 1/14 (CI 1–31%) | 1/14 | 0.21 | $0.005 |

- **Opus read past every trap.** Its two comments were fair: a non-blocking note that `!= "reject"` fails open if the schema ever loosens (it said the change was equivalent today), and a nitpick that `>= max_bytes + 1` is equivalent but harder to read. Across both harmless sets, that's 0 false alarms in 24 for Opus (CI 0–14%).
- **Haiku fell for the slice.** It blocked `digits[5:]` as "not the last four digits", missing the length-9 guard on the same line. That's why `dev` is for iterating and `report` is for reviews people act on.
- **Verification didn't change Opus's results here,** because Opus's first pass raised nothing it needed to drop. Its value showed on the original set and on Haiku.
- **Building the checker found a bug in the method.** The first version shared the app's virtualenv with each copy, and `uv run` re-pointed the app's editable install at the temporary copy, breaking the real checkout once the copy was deleted. Each copy now builds its own virtualenv from uv's cache, which adds a few seconds per change, and the control change proves the copy's code is what gets tested. [agent-eval-gate](https://github.com/dholman7/agent-eval-gate)'s agent workspaces had the same shared-venv pattern and got the same fix.

**Spot check on real pull requests.** Four test-only PRs that [Storywright](https://github.com/dholman7/storywright) opened from Jira stories ([#1](https://github.com/dholman7/vendor-intake-app/pull/1)–[#4](https://github.com/dholman7/vendor-intake-app/pull/4)), reviewed without posting. Every comment was then checked by hand against the code. Opus 5.5 (`report`) is the profile meant for real reviews; Haiku 5.5 (`dev`) is the cheap one for iterating, and these numbers show why:

| Profile | Comments | Held up | Cost |
|---|---|---|---|
| `report`, Claude Opus 5.5, 1 pass on #1–#4 | 2 | 2 | $0.13 |
| `dev`, Claude Haiku 5.5, 2 passes on #1–#3, 1 on #4 | 5 | 1 | $0.007 |

What Opus said, both correct:

- #3: the success rows only check for a 201, so a server that kept the client's declared content type would still pass. Asserting the `content_type` the API returns would test what the story actually says.
- #4: the "stored without spaces" test reads the note from the POST response instead of re-reading it. That's minor here, because the response is the saved object, but the test's name promises more than it checks.

What Haiku said:

- #1: the test didn't re-read the extraction after the 403. Correct, and applied before merging, which is why Opus, reviewing the final diff, had nothing to say about it. But Haiku's suggestion block was prose naming a route that doesn't exist.
- #2: the poll throws a TypeError on an error response. Wrong, since `&&` short-circuits first.
- #3: "draft vendor" is an unfounded assumption. Wrong, since vendors default to `draft`.
- #4: the status check may race. Wrong, since the extraction is created as `pending_review` in the same transaction. Also on #4: a 422 might still store the note. Speculative, since validation fails before the handler runs.

Haiku's two passes on #1–#3 surfaced different comments. On both profiles, the verifier dropped speculative findings before they shipped. The prose suggestion led to a deterministic guard: a suggestion must start at the cited line's indentation, and it's removed whenever a finding moves to another line, since GitHub would apply it there. Four PRs are a spot check, not a measurement; the harmless sets above are the measurement.

One finding came from building the eval itself: the first scoring only credited comments near *added* lines, so bugs that only delete a line could never count as caught. Deletions now mark where the line used to be, and there's a regression test.

### Use it in Claude Code

```text
/plugin marketplace add dholman7/agent-dev-skills
/plugin install agent-dev-skills@dholman7-skills
```

Then ask Claude to review a pull request. The skill gathers the PR with `gh`, reviews, verifies, and shows you the comments. It posts only after you approve.

### Use it in CI

```yaml
# .github/workflows/review.yml
on:
  pull_request:
    types: [opened, synchronize]
permissions:
  contents: read
  pull-requests: write
jobs:
  review:
    # Secrets aren't available to pull requests from forks.
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: dholman7/agent-dev-skills@main
        with:
          anthropic-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
          profile: report   # Claude Opus 5.5; "dev" for Claude Haiku 5.5 while iterating
```

The action's own third-party dependency is pinned to a commit SHA, because it runs with write access to pull requests.

Or run it locally:

```bash
uv sync
review-loop diff change.diff --root .                         # review a local diff
review-loop pr --repo owner/name --pr 42                       # print a review
review-loop pr --repo owner/name --pr 42 --post                # post it
review-loop eval --bugs ../agent-eval-gate/benchmarks/vendor-intake/v2/bugs.yaml --target ../vendor-intake-app
review-loop eval --clean evals/review/clean-hard.yaml --target ../vendor-intake-app   # false alarms only
```

`REVIEW_LOOP_PROFILE` picks the model. The CLI defaults to `dev` (Claude Haiku 5.5) for cheap local iteration, and the GitHub Action defaults to `report` (Claude Opus 5.5), the profile for reviews people act on.

## playwright-testing

Thirteen rules for Playwright tests (PW-1 to PW-13), such as: shared setup lives in fixtures and a data factory instead of in spec files; set up data through the API; drive UI through page objects; no credentials in specs; no fixed waits; no conditional assertions; status checks show the response body. Nine are enforced, one of them partly, by [`eslint.config.mjs`](plugins/agent-dev-skills/skills/playwright-testing/eslint.config.mjs), shipped next to the skill so a repo can copy it.

Most rules came from real gaps (the others record practices worth keeping): an audit of [vendor-intake-app](https://github.com/dholman7/vendor-intake-app)'s e2e suite found duplicated setup in six specs, hardcoded credentials, conditional assertions, and no lint. That repo now follows the rules ([`e2e/AGENTS.md`](https://github.com/dholman7/vendor-intake-app/blob/main/e2e/AGENTS.md)). Linting its old suite with these rules raises 143 errors, 95 of them locators written inline in specs; the refactored suite raises none, and still catches the same seeded bugs.

The skill ends with what to do when a new gap turns up: fix it, then add or sharpen a rule with a detector, both as pull requests. A repo's own `AGENTS.md` says how each rule applies there, and Claude Code, Cursor, and Storywright read it before writing tests.

## test-audit

Audits a repo's Playwright tests and reports each problem once per place it occurs, as a known rule broken (PW-n) or a new kind of gap with a proposed rule. Like review-loop, every finding must quote real code, and a second pass re-checks each one against the code before it's reported. With the playwright-testing standard, that second pass judges by the standard's rules rather than taste.

```bash
test-audit scan --repo ../vendor-intake-app                 # audit the working tree
test-audit scan --repo ../vendor-intake-app --ref 2db8acd   # or any commit
test-audit scan --repo ../vendor-intake-app --blind         # no standard: general best practice
```

`TEST_AUDIT_PROFILE` picks the model: `dev` (Claude Haiku 5.5, the default) or `report` (Claude Opus 5.5).

### Results

Scored against [`evals/audit/vendor-intake-2db8acd.yaml`](evals/audit/vendor-intake-2db8acd.yaml): the 22 gaps of 10 kinds in vendor-intake-app's e2e suite at `2db8acd`, the commit before they were fixed, each located by hand. A finding counts only if it cites the right file within three lines and names the gap.

| Run | Gaps found | Kinds found | Cost per audit |
|---|---|---|---|
| Claude Opus 5.5, with the standard (3 runs) | **21/22 every run** (CI 78–99%) | 10/10 | about $0.30 |
| Claude Opus 5.5, blind | 12/22 | 6/10 | about $0.19 |
| Claude Haiku 5.5, with the standard | 16/22 | 8/10 | under 1 cent |
| Claude Haiku 5.5, blind | 8/22 | 3/10 | under 1 cent |

- **It found more than the hand audit did.** Eleven of Opus's twelve other findings on the same commit were real problems I had missed: setup requests whose status is never checked, an accessibility library imported directly, tests missing Arrange-Act-Assert structure, and a "stored" check that only read the response.
- **It created a rule.** Its first run reported the ignored setup responses as a gap no rule covered. That became PW-13, with a lint rule.
- **It checked my fix.** Run against the suite after [vendor-intake-app#6](https://github.com/dholman7/vendor-intake-app/pull/6), it reported 12 problems, 11 of which held up: ten locators still written inline in UI specs, and tests covering two behaviors. After those fixes, it found two more real ones. After that, only two judgment calls remained, and they became written exceptions in PW-6 and PW-10.
- **The standard matters more than the model.** Blind, Opus found 12 of 22. Given the standard, it found 21, and Haiku 16.
- **The second pass needed the standard too.** Before it saw the standard, it dropped correct findings as "style" (an inline type import, missing Arrange-Act-Assert comments), and Opus found 17 and 19 of the gaps. Once it judged by the standard's rules, Opus found 21 of 22 in all three runs.
- **One gap is consistently missed:** a UI test that builds its data through the UI. Opus flags the suite's deliberate end-to-end journey test for that instead.

The ground truth is my own hand audit, and the match on wording uses keywords. Those were widened once, after the first run described two gaps as "ignored" and "never verified". Both are noted in the YAML.

## Not in v1

- Self-healing: an opt-in step that proposes fixes in an isolated job and applies them only after a deterministic policy check
- test-audit fixing what it finds and opening the pull requests itself, on a schedule; today a person applies the fixes
- More skills: generate-tests, investigate-ci-failure (built on [ci-context-mcp](https://github.com/dholman7/ci-context-mcp)), and review-api-changes

Written independently: no code, prompts, or names come from any employer's internal tools.

## License

[MIT](LICENSE)
