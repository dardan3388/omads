# BACKLOG.md

Visible task list for this repository.

This file is the source of truth for active work, priorities, and next steps.
Use `CHANGELOG.md` for shipped changes and `docs/architecture.md` for durable technical context.

## Usage

- New agents should read `AGENTS.md`, `PROJECT_RULES.md`, and then this file.
- Open work lives here, not hidden in narrative history files.
- Work from highest priority downward unless the user explicitly changes direction.
- When something is finished, remove it from here or mark it done and document the important outcome in `CHANGELOG.md` if it is noteworthy.

## Now

### Runtime Follow-up: Deeper Session Isolation

The most visible session leak has been reduced:

- Builder/review task settings are now frozen from the initiating WebSocket session.
- Task stream events are now sent back only to the initiating client instead of every connected client.
- Project switching and settings saves no longer broadcast repo/theme/builder changes to every open browser session.

The remaining follow-up is deeper ownership isolation:

- Project switching still persists one global `target_repo`, even though live task execution now uses the session snapshot.
- A later phase should decide whether OMADS stays intentionally single-task/global-stop, or whether task ownership should become fully session-bound.

### Feature: GitHub Integration v2 (OAuth Device Flow + New GUI)

Complete rebuild of the GitHub integration. PAT-based authentication is being replaced by the OAuth Device Flow.
This direction came out of an architecture discussion between Claude Code and Codex on 2026-03-28.

#### Why the rebuild?
- PATs are not user-friendly because users must create them manually, manage scopes, and manually re-authorize new repos.
- GitHub does not recommend PATs for third-party applications.
- The `-c remote.origin.url=` override was ignored by Git.
- Error messages appeared in German instead of English.

#### Auth: OAuth App + Device Flow

- Register one central OMADS OAuth App (`client_id` in code, no `client_secret` required).
- Device Flow: user sees a code, opens `github.com/login/device`, authorizes, done.
- The token should not expire regularly; re-auth should be needed only after revocation.
- On `401` or `403`, OMADS should offer re-auth automatically.
- For Git push/pull, try native Git credentials first and use the OAuth token only as a fallback.

#### GUI Architecture (3 Integration Points, No Mega Tab)

**1. Header:** Small status chip (connected / not connected)
**2. Start screen / project sidebar:** Actions (`New repo` | `Open repo` | `Review repo`)
**3. Open project:** Branch, remote status, pull/push controls in the sidebar

#### New Features

**Repo picker with search:**
- Searchable list of the user's repos, including org filtering.
- Direct input for `owner/repo` or a GitHub URL.
- Tabs: `Mine` | `Organizations` | `Recent` | `URL`

**Create new repo:**
- Compact modal with name, public/private, README, and `.gitignore`.
- After creation, automatically clone the repo and open it as an OMADS project.

**Review an external repo:**
- User enters `owner/repo` and OMADS validates it through the API.
- Quick look: README and metadata via API only.
- Deep review: temporary clone followed by a full review.

#### Git Ops Modal UX Fixes
- Auto-refresh after push/pull.
- For empty repos, disable buttons and show a clear hint instead of a cryptic error.
- Clear the commit-message input after a commit.

#### Implementation Phases
1. Rework the OAuth Device Flow backend in `github.py`.
2. Update the auth routes in `routes.py`.
3. Rebuild the auth UI as a Device Flow modal in `github_ui.js`.
4. Add the searchable repo picker and create-repo flow.
5. Finish the Git ops modal UX fixes.
6. Add external repo review via temporary clone.

#### Security (must remain in place)
- Never expose the token to the browser; only expose auth status.
- Never write the token into `.git/config`.
- Keep all subprocess calls on `shell=False`.
- Validate `full_name` against `^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$`.
- Scrub token-related errors before any log or user-visible error output.

### Hardening: Copilot Audit Quick Fixes

Validated improvements derived from a Copilot audit:

1. ~~**`Math.random()` ID -> counter** (`chat_ui.js`) — Generate detail toggle IDs via a counter instead of `Math.random()`.~~
2. ~~**Check CWD existence before `Popen`** (`builder_flow.py`, `review_flow.py`) — Verify that `target_repo` still exists before every `subprocess.Popen` call instead of surfacing a cryptic OS error.~~
3. **Expand tests** — Fill the previously empty `tests/` directory and cover builder-flow and review-flow edge cases before moving on to CI/CD.

### Smoke Tests

- Re-run the short live smoke test for `Codex -> Claude Code -> Codex` on a clean working tree to verify the tighter limited-data synthesis prompt under a real Claude rate-limit.
- Phase 2 (runtime module split) was completed on 2026-03-23 by extracting both `review_flow.py` and `builder_flow.py` out of `runtime.py`.
- Phase 3 (frontend module split) was completed on 2026-03-23 by moving the large inline GUI script into `src/omads/gui/static/js/` browser modules.
- Phase 4 (timeline paging / bounded loading) was completed on 2026-03-23 by adding paged timeline reads plus on-demand older-event loading in the GUI.
- The previously scheduled setup, GUI, docs, browser E2E, and configurable review-pipeline tasks were completed on 2026-03-22 and moved into `CHANGELOG.md`.
