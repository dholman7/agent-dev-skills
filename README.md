# agent-dev-skills

Claude Code skills for engineering work. The first is **review-loop**: pull request review in which every comment is checked against the code before it reaches the author, and new commits get a re-review that re-checks earlier comments.

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
- **The harmless set is too easy to measure precision.** Even unverified reviews raised no false alarms. A harder set, with refactors that look risky but aren't, is the next addition.
- **The misses are subtle:** a field quietly deleted from the extraction spec, a sort order flipped to descending (which can look intentional), and `<=` changed to `<` on a 30-day window.

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

Haiku's two passes on #1–#3 surfaced different comments. On both profiles, the verifier dropped speculative findings before they shipped. The prose suggestion led to a deterministic guard: a suggestion must start at the cited line's indentation, and it's removed whenever a finding moves to another line, since GitHub would apply it there. Four PRs are a spot check, not a measurement; the harder harmless set is still the next addition.

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
```

`REVIEW_LOOP_PROFILE` picks the model. The CLI defaults to `dev` (Claude Haiku 5.5) for cheap local iteration, and the GitHub Action defaults to `report` (Claude Opus 5.5), the profile for reviews people act on.

## Not in v1

- Self-healing: an opt-in step that proposes fixes in an isolated job and applies them only after a deterministic policy check
- A harder harmless-change set, to measure false alarms properly
- More skills: generate-tests, investigate-ci-failure (built on [ci-context-mcp](https://github.com/dholman7/ci-context-mcp)), and review-api-changes

Written independently: no code, prompts, or names come from any employer's internal tools.

## License

[MIT](LICENSE)
