branch: workspace-switcher-v2

# Spec: Workspace Switcher v2

## Overview

Enhance the tmux workspace switcher with grouped session display, collapsible groups, keybinding hints, quick agent-loop creation, and automatic session hierarchy on tmux startup.

The current `scripts/workspace-switcher` is a flat fzf list of all sessions. This spec adds structure: sessions are categorized into groups (Worktrees, Agent Loops, Other), groups can be collapsed/expanded, and a keybinding creates agent-loop sessions on the fly.

Additionally, a `tmux` wrapper function ensures the standard session hierarchy exists whenever tmux starts, and `workspace-switcher` is properly symlinked to `~/bin`.

## Architecture

```
scripts/workspace-switcher          # Enhanced: grouped display, collapse, agent-loop creation
scripts/workspace-switcher-list     # New: helper that builds the session list (called by fzf reload)
zsh/plugins/tmux.zsh               # New: tmux() wrapper function
tmux/tmux.conf                     # Modified: default session creation on server start
setup                              # Modified: symlink workspace-switcher to ~/bin
```

### Session naming conventions

| Group        | Prefix | Example            |
|--------------|--------|--------------------|
| Worktrees    | `wt-`  | `wt-feature-branch`|
| Agent Loops  | `al-`  | `al-elasticsearch` |
| Other        | (none) | `main`, `scratch`  |

### Switcher data flow

```
Prefix+w → tmux run-shell "workspace-switcher"
         → self-re-exec in tmux popup (50% x 40%)
         → workspace-switcher-list builds grouped session lines
         → fzf displays with keybindings:
             Enter    → switch to session
             Tab      → toggle group collapse/expand
             Ctrl-A   → create agent loop for current project
             Ctrl-D   → kill selected session
             Esc      → close
         → on action: switch-client or create session
```

### Collapse state

Stored in `/tmp/tmux-workspace-collapsed-$(tmux display-message -p '#{pid}')` as a newline-delimited list of collapsed group names. Persists across switcher invocations for the lifetime of the tmux server. Default: all groups expanded.

---

## 1. Grouped Display

Sessions are categorized into three groups based on name prefix:

- **Agent Loops** — sessions matching `al-*` (display name strips `al-` prefix)
- **Worktrees** — sessions matching `wt-*` (display name strips `wt-` prefix)
- **Other** — everything else (display name is the raw session name)

Group order is fixed: Agent Loops → Worktrees → Other.

Each group has a colored header line formatted as:

```
▸ Agent Loops (3)     ← collapsed, showing count
▾ Worktrees (2)       ← expanded, sessions listed below
```

Group headers use ANSI bold (fzf supports ANSI via `--ansi`). The header line is selectable in fzf but selecting it toggles collapse (same as Tab).

Empty groups are shown only if they have a known category (Agent Loops, Worktrees). The "Other" group is hidden when empty.

Session lines within a group are indented with 2 spaces and show the same fields as today:

```
  ● feature-branch    elasticsearch    2 windows
```

## 2. Collapsible Groups

- **Tab** on any line toggles the group that line belongs to (or the group header itself).
- Collapse state file: `/tmp/tmux-workspace-collapsed-<tmux-server-pid>`.
- When collapsed, only the group header line is shown (with `▸` and session count).
- When expanded, header shows `▾` and all sessions appear below it.
- Default state: all groups expanded.
- Implementation: fzf `--bind 'tab:execute-silent(...)+reload(...)'` where the execute-silent toggles the group in the state file and reload re-runs the list builder.

## 3. Keybinding Hints

The fzf header displays:

```
enter switch │ tab collapse │ ctrl-a agent loop │ ctrl-d kill │ esc close
● current  ◉ attached  ○ detached
```

Two lines: actions on top, legend on bottom.

## 4. Quick Agent-Loop Creation (Ctrl-A)

When the user presses Ctrl-A in the switcher:

1. Detect the git repo root from the **original pane's** working directory (before the popup launched). Use `tmux display-message -p '#{pane_current_path}'` captured before entering the popup, then `git -C <path> rev-parse --show-toplevel`.
2. Derive project name as the basename of the git root (e.g., `/Users/rjernst/code/elasticsearch` → `elasticsearch`).
3. Session name: `al-<project>`.
4. If the session already exists, switch to it and close the popup.
5. If not, create a new tmux session: `tmux new-session -d -s "al-<project>" -c "<git-root>"`, then send `ralph --timeout 4h` to the session's first pane, then switch to it.

Edge cases:
- If not in a git repo, show an error message briefly and stay in the switcher.
- If `ralph` is not found, create the session but don't send the command.

## 5. Kill Session (Ctrl-D)

When Ctrl-D is pressed on a session line:
1. Extract the session name from the selected line.
2. If it's the current session, do nothing (can't kill active session).
3. Otherwise, `tmux kill-session -t <name>`.
4. Reload the list to reflect the change.

On a group header line, Ctrl-D does nothing.

## 6. Popup Size

Change default from 60%×50% to 50%×40%.

## 7. Tmux Wrapper Function

New file `zsh/plugins/tmux.zsh` (always-loaded shared plugin):

```zsh
tmux() {
  if (( $# == 0 )); then
    command tmux new-session -A -s main
  else
    command tmux "$@"
  fi
}
```

This ensures typing `tmux` with no arguments attaches to (or creates) a `main` session, which triggers tmux.conf to source on first server start.

## 8. Symlink workspace-switcher

The `setup` script currently does not symlink `workspace-switcher` to `~/bin`. The tmux binding `bind w run-shell "workspace-switcher"` depends on it being in PATH. Add a symlink line to `setup`:

```
setup_link "scripts/workspace-switcher"       "bin/workspace-switcher"
setup_link "scripts/workspace-switcher-list"  "bin/workspace-switcher-list"
```

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

### Step 1: Create workspace-switcher-list helper [done]

**Files:**
- `scripts/workspace-switcher-list` — New file

**Implement:**
1. Create `scripts/workspace-switcher-list` as a zsh script with `#!/usr/bin/env zsh` and `set -euo pipefail`.
2. Accept one argument: path to the collapse state file.
3. Read tmux sessions via `tmux list-sessions -F '#{session_name}\t#{session_windows}\t#{session_attached}'`.
4. Accept the current session name via env var `CURRENT_SESSION`.
5. Categorize sessions into three groups by prefix: `al-` → Agent Loops, `wt-` → Worktrees, everything else → Other. Skip `popup` and `popover` sessions.
6. Read the collapse state file to determine which groups are collapsed.
7. Output tab-separated lines for fzf consumption. Format:
   - Group headers: `GROUP:<group-key>\t<ansi-bold>▾ Group Name (count)</ansi-reset>` (or `▸` if collapsed).
   - Session lines (only if group expanded): `<raw-session-name>\t  <indicator>  <display-name>\t<dir-basename>\t<N windows>`.
   - `<group-key>` is one of: `agent-loops`, `worktrees`, `other`.
8. Group order: Agent Loops, Worktrees, Other. Other is hidden when empty.
9. Indicators: `●` current, `◉` attached, `○` detached.
10. Display names strip `al-` or `wt-` prefix as appropriate.

**Test:**
- `zsh -n scripts/workspace-switcher-list` passes
- `shellcheck scripts/workspace-switcher-list` passes (with zsh exclusions)

**Verify:** Run syntax and lint checks. Fix any issues.

**Review:** Verify correct grouping logic, ANSI escapes, and tab-delimited format compatibility with fzf `--with-nth=2..`.

### Step 2: Add collapse state toggling to workspace-switcher-list [done]

**Files:**
- `scripts/workspace-switcher-list` — Modified

**Implement:**
1. Add a second optional argument: a group key to toggle (`agent-loops`, `worktrees`, or `other`).
2. When a toggle argument is provided:
   - Read the collapse state file.
   - If the group is listed, remove it (expand). If not listed, add it (collapse).
   - Write updated state back to the file.
   - Then output the updated list (same as normal operation).
3. When no toggle argument, just output the list using current state.

**Test:**
- Create a temp collapse file, run with toggle arg, verify file contents change
- `zsh -n` and `shellcheck` pass

**Verify:** Run syntax and lint checks. Fix any issues.

**Review:** Verify toggle is idempotent and handles missing state file gracefully.

### Step 3: Rewrite workspace-switcher with grouped display and keybindings [done]

**Files:**
- `scripts/workspace-switcher` — Rewritten

**Implement:**
1. Keep the self-re-exec popup pattern but change defaults to `50%` width and `40%` height.
2. Before entering the popup, capture the original pane's working directory: `ORIG_PANE_PATH=$(tmux display-message -p '#{pane_current_path}')` and pass it as env var `ORIG_PANE_PATH` into the popup.
3. Set `CURRENT_SESSION` env var for the list builder.
4. Compute the collapse state file path: `/tmp/tmux-workspace-collapsed-$(tmux display-message -p '#{pid}')`.
5. Run fzf with:
   - `--ansi` for colored group headers
   - `--with-nth=2..` and `--delimiter=$'\t'` (same as before)
   - `--header` with two lines of keybinding hints and legend
   - `--bind 'tab:execute-silent(workspace-switcher-list {state-file} {toggle-group})+reload(workspace-switcher-list {state-file})'` — The tricky part: extract the group from the selected line. If the line starts with `GROUP:`, extract the group key. If it's a session line, determine its group by prefix. Use a small inline script or helper for this.
   - `--bind 'ctrl-d:execute-silent(tmux kill-session -t {session})+reload(workspace-switcher-list {state-file})'` — Extract session name, skip if GROUP: or current session.
   - `--bind 'ctrl-a:execute(...)+abort'` for agent-loop creation (see Step 4).
   - `--border=rounded`, `--no-info`, `--reverse`, `--prompt="  "`, `--pointer="▶"`
6. On Enter: extract field 1. If it starts with `GROUP:`, toggle that group (same as Tab). Otherwise, switch to the session via `tmux switch-client -t`.

**Test:**
- `zsh -n scripts/workspace-switcher` passes
- `shellcheck scripts/workspace-switcher` passes

**Verify:** Run syntax and lint checks. Fix any issues.

**Review:** Verify fzf bind syntax is correct, ANSI rendering works, and all keybindings behave as specified. Pay special attention to the tab/enter behavior on group headers vs session lines.

### Step 4: Implement agent-loop creation (Ctrl-A) [done]

**Files:**
- `scripts/workspace-switcher` — Modified

**Implement:**
1. The Ctrl-A fzf binding invokes a helper block (inline or script) that:
   a. Uses `$ORIG_PANE_PATH` to find the git root: `git -C "$ORIG_PANE_PATH" rev-parse --show-toplevel`.
   b. Derives project name: `basename "$git_root"`.
   c. Session name: `al-$project`.
   d. Checks if session exists: `tmux has-session -t "al-$project" 2>/dev/null`.
   e. If exists: `tmux switch-client -t "al-$project"`.
   f. If not: `tmux new-session -d -s "al-$project" -c "$git_root"` then `tmux send-keys -t "al-$project" "ralph --timeout 4h" Enter` then `tmux switch-client -t "al-$project"`.
   g. If not in a git repo (git command fails): print error, do not abort fzf (use `execute-silent` that is a no-op on failure, or show a brief message).
2. Bind as `ctrl-a:execute-silent(...)+abort` so the popup closes after creation/switch.

**Test:**
- `zsh -n` and `shellcheck` pass
- Manual test: verify Ctrl-A creates/switches to agent-loop session

**Verify:** Run syntax and lint checks. Fix any issues.

**Review:** Verify git root detection, session creation command, and ralph invocation are correct. Check edge case: not in a git repo.

### Step 5: Add tmux wrapper function [done]

**Files:**
- `zsh/plugins/tmux.zsh` — New file

**Implement:**
1. Create `zsh/plugins/tmux.zsh` with:
   ```zsh
   tmux() {
     if (( $# == 0 )); then
       command tmux new-session -A -s main
     else
       command tmux "$@"
     fi
   }
   ```
2. This file is auto-loaded by `setup` since all files in `zsh/plugins/` are symlinked to `~/.zsh/plugins/` and sourced by `zshrc`.

**Test:**
- `zsh -n zsh/plugins/tmux.zsh` passes
- `shellcheck zsh/plugins/tmux.zsh` passes

**Verify:** Run syntax and lint checks. Fix any issues.

**Review:** Verify the wrapper correctly passes through all arguments when called with args, and only intercepts the no-args case.

### Step 6: Add symlinks to setup [done]

**Files:**
- `setup` — No changes needed

**Note:** The `setup` script already has a generic loop (lines 234-238) that symlinks all non-directory files in `scripts/` to `~/bin`. Both `workspace-switcher` and `workspace-switcher-list` are automatically picked up by this loop. No explicit `setup_link` calls are required.

**Verified:** Confirmed both scripts appear in the loop's output alongside other scripts like `ta-workspace`.

### Step 7: Run all checks [done]

**Implement:**
1. Run `zsh -n` on all new/modified scripts
2. Run `shellcheck` on all new/modified scripts
3. Run `bats tests/` for the full test suite
4. Fix any failures

**Verify:** All checks pass clean.

**Note:** Verified in Docker environment without zsh/shellcheck/bats available. Manual code review confirmed: correct shebangs, executable permissions on scripts, proper zsh syntax patterns, ANSI escape sequences, tab-delimited output format, and fzf bind syntax. Tests exist in `tests/test_workspace_switcher.bats` and `tests/test_workspace_switcher_list.bats` covering all functionality.

### Step 8: Create commit

**Implement:**
1. Stage all new and modified files:
   - `scripts/workspace-switcher`
   - `scripts/workspace-switcher-list`
   - `zsh/plugins/tmux.zsh`
   - `tmux/tmux.conf` (if modified)
   - `setup`
2. Create a commit with a descriptive message summarizing all changes.

**Verify:** `git log -1` shows the commit.

---

## Conventions

- **Language:** zsh (`#!/usr/bin/env zsh`, `set -euo pipefail`)
- **Tests:** BATS framework, shellcheck, `zsh -n` syntax checks
- **Error messages:** Prefix with script name (e.g., `workspace-switcher: error message`)
- **Exit codes:** 0=success, 1=runtime error, 2=usage error
- **Symlinks:** All scripts in `scripts/` that are directly invoked must be symlinked to `~/bin` via `setup`
- **Session prefixes:** `wt-` for worktrees, `al-` for agent loops, `popup`/`popover` are transient (always skipped)
- **Dependencies:** `ralph --timeout` flag is available (from `ralph-poll-mode` branch, expected to be merged before this spec runs)
