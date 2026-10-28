---
name: review-loop
description: Review a GitHub pull request with verified findings. Reviews the diff, checks every citation against the code, re-derives each finding before reporting it, and writes Conventional Comments. Re-reviews new commits and re-checks earlier comments. Use when asked to review a PR, re-review after changes, or check whether review comments were addressed.
---

# review-loop

Review a pull request so that every comment that reaches the author is grounded in the code. A confident wrong comment costs more trust than a missed nit.

## 1. Gather

```bash
gh pr view <PR> --json number,title,body,headRefOid,baseRefOid,url
gh pr diff <PR>
```

Read each changed file at the head commit, not only the diff. Context outside the hunk often decides whether a change is wrong.

If this is a re-review, find the last commit reviewed: an earlier review whose body contains `<!-- review-loop sha=<sha> -->`. Review only `git diff <sha>..<head>`.

## 2. Review

Look for what a careful senior engineer would block on:

- **Correctness:** wrong conditions, off-by-one and boundary errors (`<` vs `<=`), inverted comparisons, missing branches, wrong status codes.
- **Security:** authorization and tenant checks removed or bypassed, injection, secrets, data exposure.
- **Error handling:** swallowed exceptions, missing validation, retries without limits.
- **Tests:** assertions weakened or deleted, behavior changes with no test.
- **API:** breaking changes to request or response shapes, error codes, or defaults.

Record each finding with: file, line in the new version, a label, whether it blocks, and the **exact line of code it's about**, copied character for character.

Report nothing rather than speculate. An empty review is a good review when the change is fine.

## 3. Verify every finding before reporting it

For each finding, re-open the file at the cited line and re-derive the finding from the code itself. Don't re-read your own note: that anchors on the first reading.

1. **Check the citation literally.** The quoted code must exist at or near the cited line. If it's elsewhere, move the finding there. If it doesn't exist, drop the finding.
2. **Check it's commentable.** GitHub only accepts comments on lines inside the diff. A finding about unchanged code goes in the review summary, not on a line.
3. **Decide:** confirm it, **demote** it (real, but shouldn't block), **retarget** it (real, but on a different line), or **drop** it (the code doesn't show the problem).

Keep a list of what was dropped or demoted, with one-line reasons, and report it.

## 4. Write the comments

Use [Conventional Comments](https://conventionalcomments.org):

```text
issue (blocking): expiry date compared as local time

`parse_expiry()` drops the timezone, so a certificate expiring at midnight UTC shows as valid
until evening in UTC-5.

```suggestion
expires_at = datetime.fromisoformat(raw).astimezone(UTC)
```
```

- Labels: `issue`, `suggestion`, `nitpick`, `question`. Use `(blocking)` only for a real defect; everything else is `(non-blocking)`.
- Lead with the problem. Add a `suggestion` block only when you can name the exact replacement for the cited line.
- Keep each comment short and kind, and give the author latitude.

## 5. Post only with approval

Show the user the exact comments, the PR, and the commit they'd be posted on, and wait for a clear go-ahead before posting. Then post one review with event `COMMENT`:

```bash
gh api repos/<owner>/<repo>/pulls/<PR>/reviews --method POST --input review.json
```

`review.json` holds `commit_id`, a short summary body ending in `<!-- review-loop sha=<head> -->`, `event: "COMMENT"`, and `comments` (`path`, `line`, `side: "RIGHT"`, `body`). End every comment body with `<!-- review-loop -->` so a later re-review can recognize its own threads.

- Never `APPROVE` or `REQUEST_CHANGES`. A person decides the merge.
- **Never resolve a thread a person started.** On re-review, a thread whose first comment carries `<!-- review-loop -->` may be resolved, with approval, once the code shows the problem is fixed. Reply with what changed first.

## Headless use

The same pipeline runs without Claude Code, for CI:

```bash
review-loop pr --repo owner/name --pr 42           # print the review
review-loop pr --repo owner/name --pr 42 --post    # post it (needs pull requests: write)
```
