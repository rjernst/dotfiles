# Spec: `ta` — Terminal Agent Tool

## Overview

Build a `ta` CLI tool for the dotfiles repo that provides worktree management, tmux workspace sessions, fork-and-focus workflows, session reporting, and tmux introspection. The tool is written in zsh (matching the repo's conventions) with BATS tests for each component.

Unlike simian-dotfiles which uses a Python CLI with Elasticsearch dependencies, this implementation is self-contained: shell scripts, git plumbing, and tmux — no external services required.

## Architecture

```
scripts/
  ta                        # Main entry point (subcommand dispatcher)
  ta-wt                     # Worktree manager (replaces git-make-worktree)
  ta-workspace              # Tmux workspace session manager
  ta-ff                     # Fork-and-focus session creator
  ta-report                 # Session report generator
  ta-tmux                   # Tmux introspection
tests/
  test_ta_wt.bats           # Worktree tests
  test_ta_workspace.bats    # Workspace tests
  test_ta_ff.bats           # Fork-and-focus tests
  test_ta_report.bats       # Report tests
  test_ta_tmux.bats         # Tmux introspection tests
```

The main `ta` script dispatches to sub-scripts: `ta wt list` runs `ta-wt list`. Each sub-script is independently executable for testing and direct use.

---

## 1. `ta wt` — Worktree Manager

Replaces `git-make-worktree` with a full worktree lifecycle tool. Operates on the current git repo (uses `git -C` or `$PWD`).

### Subcommands

#### `ta wt list`

List all worktrees for the current repo with enriched status.

```
ta wt list [--json] [--full]
```

**Default (text) output:**
```
BRANCH              STATUS       AHEAD  BEHIND  PATH
main                current      0      0       /Users/me/code/elasticsearch
feature/fix-thing   clean        3      1       /Users/me/code/es-fix-thing
bugfix/oom          dirty(2M)    1      0       /Users/me/code/es-oom
```

**How it works:**
1. Run `git worktree list --porcelain` to get all worktrees
2. For each worktree, determine:
   - Branch name (from HEAD or detached state)
   - Dirty status via `git -C <path> status --porcelain` (count staged/modified/untracked)
   - Ahead/behind main via `git rev-list --left-right --count main...<branch>`
   - Whether it's the current worktree
3. With `--full`: also show last commit message and date
4. With `--json`: output as JSON array

#### `ta wt create <branch> [path]`

Create a worktree tracking a remote branch.

```
ta wt create <branch> [path] [--remote <remote>]
```

- `branch`: Remote branch name to track
- `path`: Local path for the worktree (default: `~/worktrees/<repo-name>/<branch>` with `/` in branch names replaced by `-`)
- `--remote`: Remote to track (default: `upstream`, falls back to `origin`)

**How it works:**
1. Validate the branch exists on the remote (`git ls-remote --exit-code --heads <remote> refs/heads/<branch>`)
2. Validate the target path doesn't exist
3. Run `git worktree add --track -b <branch> <path> <remote>/<branch>`
4. Print the created path

**Replaces:** `scripts/git-make-worktree` (which is hardcoded to `upstream` and requires both args).

#### `ta wt remove <branch>`

Remove a worktree by branch name.

```
ta wt remove <branch> [--force]
```

**How it works:**
1. Find the worktree path for the given branch (from `git worktree list --porcelain`)
2. Check for uncommitted changes; refuse to remove if dirty (unless `--force`)
3. Run `git worktree remove <path>` (add `--force` if flag set)
4. Delete the local branch: `git branch -d <branch>` (or `-D` with `--force`)

#### `ta wt prune`

Remove worktrees whose branches are fully merged into main.

```
ta wt prune [--apply]
```

**Default:** dry-run, lists what would be pruned.

**How it works:**
1. List all worktrees (excluding main and current)
2. For each, check if merged into main: `git merge-base --is-ancestor <branch> main`
3. Check for dirty working tree
4. Skip worktrees with uncommitted changes or active operations (`.git/rebase-merge`, `.git/MERGE_HEAD`)
5. Without `--apply`: print candidates with status
6. With `--apply`: run `ta wt remove <branch>` for each candidate

#### `ta wt status`

Quick one-line status of each worktree (designed for prompt/report integration).

```
ta wt status [--json]
```

**Text output:**
```
feature/fix-thing   ready       3 ahead, clean
bugfix/oom          wip         1 ahead, 2 modified
stale/old-thing     merged      0 ahead, clean
```

**Status classifications:**
- `ready` — clean, ahead of main, no unpushed commits
- `almost` — clean, ahead of main, has unpushed commits
- `wip` — dirty working tree
- `merged` — fully merged into main (prune candidate)
- `conflict` — would conflict with main
- `current` — the active worktree

---

## 2. `ta workspace` — Tmux Session Manager

Manages tmux sessions tied to worktrees. Sessions are named `wt-<sanitized-branch>`.

### Branch name sanitization

`feature/fix-thing` → `wt-feature-fix-thing` (replace `/`, spaces, special chars with `-`).

### Subcommands

#### `ta workspace create <branch>`

```
ta workspace create <branch> [--cmd <command>]
```

**How it works:**
1. Find the worktree path for `<branch>` using `ta wt list --json`
2. Sanitize branch name into session name
3. If session already exists, print message and exit
4. Create detached tmux session: `tmux new-session -d -s <session> -c <worktree_path>`
5. If `--cmd` provided, send the command to the session (default: just a shell)
6. Print session name and attach instructions

#### `ta workspace list`

```
ta workspace list
```

List all `wt-*` tmux sessions with status.

**Output:**
```
SESSION                ATTACHED  WINDOWS  CWD
wt-feature-fix-thing   0         2        /Users/me/code/es-fix-thing
wt-bugfix-oom          1         1        /Users/me/code/es-oom
```

**How it works:**
1. Run `tmux list-sessions -F '#{session_name}\t#{session_windows}\t#{session_attached}'`
2. Filter to sessions starting with `wt-`
3. For each, get CWD via `tmux display-message -t <session>: -p '#{pane_current_path}'`

#### `ta workspace attach <branch>`

```
ta workspace attach <branch>
```

**How it works:**
1. Sanitize branch to session name
2. If session doesn't exist, auto-create it via `ta workspace create`
3. If inside tmux: `tmux switch-client -t <session>`
4. If outside tmux: `tmux attach-session -t <session>`

#### `ta workspace kill <branch>`

```
ta workspace kill <branch>
```

**How it works:**
1. Sanitize branch to session name
2. Check if session exists
3. If session is attached, prompt for confirmation (skip prompt if not a tty)
4. Run `tmux kill-session -t <session>`

---

## 3. `ta ff` — Fork and Focus

Creates a new worktree + workspace in one command. "Fork" a branch off main and "focus" by opening a tmux session in it.

### Subcommands

#### `ta ff <branch> [prompt]`

```
ta ff <branch> [prompt] [--remote <remote>] [--cmd <command>]
```

**How it works:**
1. Create a new local branch from main: `git checkout -b <branch> main`
2. Push the branch to remote: `git push -u <remote> <branch>`
3. Create worktree: `ta wt create <branch>`
4. Create workspace: `ta workspace create <branch> --cmd <command>`
5. Attach to the workspace: `ta workspace attach <branch>`

If the branch already exists as a worktree, skip creation and just attach.

**With `[prompt]`:** If a prompt string is provided, pass it to the agent command. E.g.:
```
ta ff fix/null-pointer "Fix the NPE in SearchService.java"
```
Creates the branch, worktree, tmux session, and launches `claude "Fix the NPE in SearchService.java"` in it.

#### `ta ff --from-worktree <branch>`

If the worktree already exists (branch was created manually or via `ta wt create`), just create the workspace and attach.

---

## 4. `ta report`

Generates a markdown status report of all worktrees and workspaces. Designed for piping into an AI agent or reading in a terminal.

### Subcommands

#### `ta report`

```
ta report [--repo <path>]
```

**Output (markdown):**
```markdown
# Workspace Report

## Worktrees
| Branch | Status | Ahead | Behind | Workspace | Path |
|--------|--------|-------|--------|-----------|------|
| feature/fix-thing | ready | 3 | 0 | attached | /Users/me/code/es-fix |
| bugfix/oom | wip | 1 | 0 | detached | /Users/me/code/es-oom |
| stale/old | merged | 0 | 0 | — | /Users/me/code/es-old |

## Active Sessions
| Session | Windows | CWD |
|---------|---------|-----|
| wt-feature-fix-thing | 2 | /Users/me/code/es-fix |

## Main Branch
- Last fetched: 2 hours ago
- Behind upstream: 5 commits
```

**How it works:**
1. Run `ta wt status --json` to get worktree data
2. Run `ta workspace list` to get session data
3. Correlate worktrees with workspace sessions
4. Check main branch freshness: `git log -1 --format='%cr' FETCH_HEAD`
5. Check main behind upstream: `git rev-list --count main..upstream/main`
6. Render as markdown table
7. If `--repo` specified, only report on that repo; otherwise report on current repo

---

## 5. `ta tmux` — Tmux Introspection

Structured queries against the tmux server. Outputs JSON for programmatic use.

### Subcommands

#### `ta tmux sessions`

```
ta tmux sessions [--json]
```

List all tmux sessions.

**JSON output:**
```json
[{"name": "main", "windows": 3, "attached": 1, "created": "2024-01-15T10:00:00"}]
```

**How it works:**
- `tmux list-sessions -F '#{session_name}\t#{session_windows}\t#{session_attached}\t#{session_created}'`
- Parse and output as JSON (or text table)

#### `ta tmux windows`

```
ta tmux windows [--session <name>] [--json]
```

List windows, optionally filtered to a session.

#### `ta tmux panes`

```
ta tmux panes [--session <name>] [--json]
```

List panes with command, PID, and CWD.

#### `ta tmux capture <pane_id>`

```
ta tmux capture <pane_id> [--lines <n>]
```

Capture scrollback from a pane. Default 120 lines.

**Output:** JSON with `{pane_id, lines, content}`.

---

## Implementation Plan

Each step follows this structure:
1. **Implement** — Write the code
2. **Test** — Write BATS tests
3. **Verify** — Run tests, fix failures until all pass
4. **Review** — Code review for bugs, edge cases, and conventions
5. **Address feedback** — Fix review findings, re-run tests, re-review until clean
6. **Update spec** — Mark the step `[done]` and record any decisions or deviations

### Spec maintenance rules

- Mark each step `[done]` when complete.
- Record design decisions that emerged during implementation as notes under the step.
- Minor deviations (e.g. flag name changes, reordered logic) should be noted and the spec updated to match.
- Significant design changes (e.g. new subcommands, changed architecture, removed features) require pausing for user review before proceeding.

### Step 1: `ta` dispatcher and `ta wt list`

**Files:**
- `scripts/ta` — Main dispatcher
- `scripts/ta-wt` — Worktree subcommands (start with `list`)
- `tests/test_ta_wt.bats` — Tests for `ta wt list`

**Implement:**
1. Create `scripts/ta` dispatcher that finds and executes `ta-<subcommand>` scripts
2. Create `scripts/ta-wt` with `list` subcommand (text and `--json` output)
3. Parse `git worktree list --porcelain` output
4. Compute dirty status, ahead/behind main for each worktree

**Test:**
- No worktrees (just main)
- Multiple worktrees with varying states
- Dirty worktree (staged, modified, untracked)
- `--json` output structure
- `--full` output includes commit message and date

**Verify:** Run `bats tests/test_ta_wt.bats`. Fix any failures and re-run until all pass.

**Review:** Code review `ta` and `ta-wt` for correctness, edge cases (detached HEAD, bare repos, missing main branch), and adherence to conventions.

**Address feedback:** Fix all review findings. Re-run tests to confirm no regressions. Re-review if changes were substantial.

### Step 2: `ta wt create` and `ta wt remove`

**Files:**
- `scripts/ta-wt` — Add `create` and `remove` subcommands
- `tests/test_ta_wt.bats` — Additional tests

**Implement:**
1. Implement `create` with remote detection (upstream → origin fallback)
2. Implement default path generation (`../<repo>-<branch>`)
3. Implement `remove` with dirty-check safety
4. Migrate `git-make-worktree` behavior (mark as deprecated or alias)

**Test:**
- Create with explicit path
- Create with default path (verify generated path)
- Create nonexistent branch (error)
- Create with `--remote` override
- Remote fallback: upstream → origin
- Remove clean worktree
- Remove dirty worktree (refused)
- Remove with `--force`
- Remove nonexistent branch (error)

**Verify:** Run `bats tests/test_ta_wt.bats`. Fix any failures and re-run until all pass.

**Review:** Review path generation logic, remote fallback behavior, force-remove safety.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 3: `ta wt prune` and `ta wt status`

**Files:**
- `scripts/ta-wt` — Add `prune` and `status` subcommands
- `tests/test_ta_wt.bats` — Additional tests

**Implement:**
1. Implement merge detection via `git merge-base --is-ancestor`
2. Implement active-operation detection (rebase/merge in progress)
3. Implement status classification logic (ready/almost/wip/merged/conflict)
4. Implement dry-run and `--apply` for prune

**Test:**
- Prune dry-run lists merged branch as candidate
- Prune skips dirty worktrees
- Prune skips current worktree
- Prune skips main branch
- Prune `--apply` actually removes worktrees
- Status: `ready` (clean, ahead, pushed)
- Status: `almost` (clean, ahead, unpushed)
- Status: `wip` (dirty)
- Status: `merged` (ancestor of main)
- Status: `current` (active worktree)
- Status `--json` output structure

**Verify:** Run `bats tests/test_ta_wt.bats`. Fix any failures and re-run until all pass.

**Review:** Review prune safety (never removes main/current), status classification edge cases.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 4: `ta tmux` — Tmux introspection

**Files:**
- `scripts/ta-tmux` — All tmux subcommands
- `tests/test_ta_tmux.bats` — Tests

**Implement:**
1. Implement `sessions`, `windows`, `panes` with tmux format strings
2. Implement `capture` with configurable line count
3. Implement JSON output (using `jq` or manual construction)
4. Handle "no server running" gracefully

**Test:**
- Parse tmux output into structured fields (mock tmux output)
- JSON structure validation for each subcommand
- No-server-running returns empty array (not error)
- `capture` with custom `--lines` value
- Missing pane ID returns error

**Verify:** Run `bats tests/test_ta_tmux.bats`. Fix any failures and re-run until all pass.

**Review:** Review tmux format string correctness, JSON escaping for pane content with special characters.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 5: `ta workspace` — Session management

**Files:**
- `scripts/ta-workspace` — All workspace subcommands
- `tests/test_ta_workspace.bats` — Tests

**Implement:**
1. Implement branch name sanitization function
2. Implement `create` with worktree lookup
3. Implement `list` filtering `wt-*` sessions
4. Implement `attach` with auto-create and inside/outside tmux detection
5. Implement `kill` with attached-session confirmation

**Test:**
- Sanitization: `feature/foo` → `wt-feature-foo`
- Sanitization: special chars, multiple slashes, leading/trailing dashes
- Create is idempotent (second call prints message, exits 0)
- Create fails if branch has no worktree
- List only shows `wt-*` sessions
- List with no sessions prints empty message
- Kill nonexistent session returns error

**Verify:** Run `bats tests/test_ta_workspace.bats`. Fix any failures and re-run until all pass.

**Review:** Review sanitization (special chars, unicode), tmux inside/outside detection, race conditions.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 6: `ta ff` — Fork and focus

**Files:**
- `scripts/ta-ff` — Fork-and-focus command
- `tests/test_ta_ff.bats` — Tests

**Implement:**
1. Implement branch creation from main
2. Implement push to remote
3. Chain: branch → worktree → workspace → attach
4. Implement `--from-worktree` shortcut for existing worktrees
5. Implement prompt passthrough to agent command

**Test:**
- Full flow creates branch, worktree, and workspace
- Existing branch skips creation and attaches
- `--from-worktree` skips branch creation
- Prompt string is correctly shell-escaped
- Failure mid-chain produces clear error (no partial state left behind)

**Verify:** Run `bats tests/test_ta_ff.bats`. Fix any failures and re-run until all pass.

**Review:** Review the full create chain for failure handling (rollback on partial failure?), prompt shell-escaping safety.

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 7: `ta report`

**Files:**
- `scripts/ta-report` — Report generator
- `tests/test_ta_report.bats` — Tests

**Implement:**
1. Implement worktree → workspace correlation
2. Implement main branch freshness check
3. Implement markdown table rendering

**Test:**
- Report with mixed worktree states renders correct table
- Report with no workspaces omits session section
- Report with stale main shows behind count
- Report with no worktrees (just main) still renders
- Markdown output is well-formed (header row, separator, data rows)

**Verify:** Run `bats tests/test_ta_report.bats`. Fix any failures and re-run until all pass.

**Review:** Review markdown formatting, edge cases (no worktrees, no tmux server).

**Address feedback:** Fix all review findings. Re-run tests. Re-review if changes were substantial.

### Step 8: Integration and cleanup

**Implement:**
1. Remove or alias `scripts/git-make-worktree` → `ta wt create`
2. Update `CLAUDE.md` with new `ta` commands
3. Add `ta` to PATH setup (if not already via scripts/)

**Verify:** Run full test suite: `bats tests/`. Ensure no existing tests broke.

**Review:** Final review of all commands for consistency (flag names, output formats, error messages, exit codes)

**Address feedback:** Fix any final findings. Re-run full test suite. Confirm clean.

---

## Conventions

- **Language:** zsh for all scripts (consistent with repo)
- **Tests:** BATS with temp git repos (same pattern as `test_git_make_worktree.bats`)
- **JSON output:** Use `jq` where available, fall back to printf-based construction
- **Error messages:** Prefix with `ta:` (e.g., `ta: branch not found`)
- **Exit codes:** 0=success, 1=runtime error, 2=usage error
- **No external services:** No Elasticsearch, no APIs. Pure git + tmux + shell.
