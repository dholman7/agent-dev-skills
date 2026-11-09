# Security

Report a vulnerability privately to danxholman@gmail.com, not in a public issue. Only `main` is supported.

In scope: anything that makes review-loop post without `--post`, approve or request changes, resolve a thread it didn't start, or expose the Anthropic or GitHub token. The same applies to test-audit and the GitHub Action.

Known limitation: diffs, PR titles, and PR bodies are untrusted and go to the model. Every finding must quote code that exists and pass a verification step before it's posted, but a crafted diff can still try to suppress or steer findings. The Action needs `pull-requests: write`; grant nothing broader.
