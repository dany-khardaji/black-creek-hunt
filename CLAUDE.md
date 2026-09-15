@AGENTS.md

# Claude Code Workflow

- Act as the implementation driver when the user requests a change. Act as a reviewer only when the user requests a review.
- Before editing, inspect the relevant code, `git status`, and the applicable section of `PLAN.md`.
- For substantial changes, state the intended scope and important assumptions briefly, then carry the requested work through verification.
- Preserve unrelated changes and keep edits inside the requested scope.
- Prefer straightforward functions and explicit data flow over unnecessary classes, patterns, or indirection.
- Add or update focused tests for behavior that can regress; reuse shared fixtures instead of repeating database setup.
- Do not commit, push, deploy, install production dependencies, or change external services unless the user asks.
- After implementation, summarize the result, list the checks run, and identify anything that remains unverified.
