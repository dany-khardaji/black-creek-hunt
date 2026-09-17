@AGENTS.md

# Claude Code Workflow

- Act as the implementation driver when the user requests a change. Act as a reviewer only when the user requests a review.
- Explain everything in plain, beginner-friendly language with simple examples, and define technical terms the first time you use them.
- Before editing, inspect the relevant code, `git status`, and the applicable section of `docs/PLAN.md`.
- For substantial changes, state the intended scope and important assumptions briefly, then carry the requested work through verification.
- Preserve unrelated changes and keep edits inside the requested scope.
- Prefer straightforward functions and explicit data flow over unnecessary classes, patterns, or indirection.
- Add or update focused tests for behavior that can regress; reuse shared fixtures instead of repeating database setup.
- Do not commit, push, deploy, install production dependencies, or change external services unless the user asks.
- After implementation, summarize the result, list the checks run, and identify anything that remains unverified.
- Only comment non-obvious logic. Use one plain-English line a beginner could understand. No jargon, no restating the code.
- Don't use shorthand or unexplained results; always say what a number, status, or term refers to and what it means.
- Anytime you state an http error code (402, 404 etc.) put in parenthesis what it means next to it so I can remember http status codes easier.
