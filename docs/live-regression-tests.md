# Live Regression Tests

Use this suite after larger OMADS changes, before public milestones, or whenever you want one repeatable live check that both Codex and Claude Code agents can follow without a long custom prompt.

## Goal

Validate the real end-to-end OMADS workflows that matter most in daily use:

- automatic builder -> breaker -> builder loops in both directions
- manual three-step review in both reviewer orders
- session-local project creation and project switching
- context continuity inside OMADS chats
- provider failure handling (`task_error` + `unlock`) when one CLI is rate-limited or misconfigured

## Prerequisites

- Run OMADS on a spare local port such as `8103`
- Authenticate both CLIs locally: `claude` and `codex`
- Prefer low-cost settings for live regression:
  - `claude_effort = low`
  - `codex_reasoning = low`
  - `codex_fast = true`
- Create temporary test repos only inside `$HOME`
- Record every case as `PASS`, `FAIL`, or `BLOCKED (provider quota/auth)`

## Recommended Temporary Workspace

Create and clean up temporary folders under `~/Downloads`:

- `omads-trial-auto-codex`
- `omads-trial-auto-claude`
- `omads-trial-manual-review-claude`
- `omads-trial-manual-review-codex`
- `omads-trial-project-a`
- `omads-trial-project-b`
- `omads-trial-cli-to-claude`

## Test Matrix

### 1. Automatic review: Codex builds, Claude reviews, Codex fixes

Settings:

- `builder_agent = codex`
- `review_first_reviewer = claude`
- `review_second_reviewer = codex`
- `auto_review = true`

Prompt:

```text
For OMADS auto-review testing, create a deliberately unsafe first draft file named unsafe_calc.py that hardcodes API_KEY = "sk-test-demo" and uses eval(input(...)) to compute arithmetic. Keep it very short.
```

Pass criteria:

- Codex creates the file
- Claude Review runs automatically
- findings are routed back to Codex
- Codex applies a follow-up fix
- final file no longer hardcodes the key and no longer uses direct `eval(input(...))`
- UI ends with `unlock`

### 2. Automatic review: Claude builds, Codex reviews, Claude fixes

Settings:

- `builder_agent = claude`
- `review_first_reviewer = claude`
- `review_second_reviewer = codex`
- `auto_review = true`

Use the same prompt as test 1.

Pass criteria:

- Claude creates the file
- Codex Review runs automatically
- findings are routed back to Claude
- Claude applies a follow-up fix
- final file is visibly safer than the first draft
- UI ends with `unlock`

### 3. Manual review: Claude -> Codex -> Claude

Seed a file such as `manual_issue.py` with:

```python
API_KEY = "sk-test-demo"

print(eval(input("expr: ")))
```

Settings:

- `builder_agent = claude`
- `review_first_reviewer = claude`
- `review_second_reviewer = codex`
- `auto_review = false`

Review request:

- scope: `project`
- focus: `security`

Pass criteria:

- all three review steps stream into the UI
- OMADS emits `review_fixes_available`
- `apply_fixes` rewrites the file
- the rewritten file removes the hardcoded key and the dangerous direct `eval(input(...))`
- UI ends with `unlock`

### 4. Manual review: Codex -> Claude -> Codex

Use the same seeded file as test 3.

Settings:

- `builder_agent = codex`
- `review_first_reviewer = codex`
- `review_second_reviewer = claude`
- `auto_review = false`

Pass criteria when both providers are healthy:

- Codex review runs as step 1
- Claude review runs as step 2
- Codex synthesis runs as step 3
- OMADS emits `review_fixes_available`

Important fallback expectation:

- if step 3 fails only because Codex synthesis is temporarily unavailable or usage-limited, OMADS should now fall back to Claude synthesis instead of dropping the whole review result
- if Codex is already unavailable in step 1, mark the test as `BLOCKED (provider quota/auth)` if OMADS still surfaces a visible `task_error` and `unlock`

### 5. CLI agent -> OMADS -> other agent dialogue

Use one terminal agent to talk to the opposite builder through OMADS.

Example with Claude selected as the builder:

1. Send: `Reply with only: Hello from Claude.`
2. Send: `Remember the token SUNBEAM-17 and reply with only: stored.`
3. Send: `What token did I ask you to remember? Reply with only the token.`

Pass criteria:

- all three turns return through OMADS
- the final answer is `SUNBEAM-17`
- no manual GUI clicking is required beyond the initial builder selection

### 6. Project creation, switching, and folder isolation

Create two temporary projects in different folders and register both through the OMADS project API or GUI.

Example tasks:

- in project A: create `project_a_marker.txt` with the single line `project a ok`
- in project B: create `project_b_marker.txt` with the single line `project b ok`
- switch back to each project and ask for the exact marker filename created earlier

Pass criteria:

- both projects can be created and selected
- each file is written only into the correct project folder
- switching back to project A or B keeps the expected project-local context

### 7. Provider failure-mode sanity check

Trigger one known failure condition such as:

- Codex usage limit
- missing auth
- invalid model override

Pass criteria:

- OMADS streams a visible error message
- OMADS emits `task_error`
- OMADS always emits `unlock`
- the UI does not hang for minutes without feedback

### 8. Optional builder-handover context test

Run this only when both Claude and Codex quotas are available.

Example:

1. Builder A creates a file and remembers a token
2. switch the builder
3. Builder B is asked to continue the task and repeat the token or inspect the file

Pass criteria:

- Builder B receives enough OMADS handover context to continue naturally
- file changes still stay inside the active project repo

## Result Template

```text
Date:
Port:
Commit:

1. Auto review Codex -> Claude -> Codex: PASS / FAIL / BLOCKED
2. Auto review Claude -> Codex -> Claude: PASS / FAIL / BLOCKED
3. Manual review Claude -> Codex -> Claude: PASS / FAIL / BLOCKED
4. Manual review Codex -> Claude -> Codex: PASS / FAIL / BLOCKED
5. CLI -> OMADS -> other agent dialogue: PASS / FAIL / BLOCKED
6. Project creation + switching + folder isolation: PASS / FAIL / BLOCKED
7. Provider failure-mode sanity: PASS / FAIL / BLOCKED
8. Optional builder-handover context: PASS / FAIL / BLOCKED

Notes:
```

## Release Guidance

Yes, tagged releases make sense.

For a public repo, keep `main` moving, but cut GitHub releases for known-good checkpoints after this suite passes. That gives users a stable rollback target, makes bug reports easier to compare, and avoids the pressure to keep every `main` commit permanently production-friendly.
