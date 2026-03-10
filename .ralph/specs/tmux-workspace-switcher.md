branch: tmux-workspace-switcher

# Spec: Tmux Workspace Switcher

## Overview

Add a fuzzy tmux session picker that opens in a centered popup overlay via `Prefix + w`. The script lists all tmux sessions with fzf, showing status indicators (current/attached/detached), stripping the `wt-` prefix from workspace session names for cleaner display, and switching to the selected session.

Based on the simian-dotfiles `bin/workspace-switcher` (`~/code/simian-dotfiles/bin/workspace-switcher`).

## Architecture

```
scripts/workspace-switcher     # New: fzf session picker (zsh, auto-symlinked to ~/bin)
tmux/tmux.conf                 # Modified: add bind-key w

Flow:
  Prefix + w  →  tmux runs workspace-switcher
                  →  script detects it's not in a popup yet
                  →  re-execs itself inside `tmux popup -E`
                  →  builds session list with indicators
                  →  pipes to fzf for fuzzy selection
                  →  switches tmux client to chosen session
```

---

## 1. Script Behavior

### Self-launching popup
When invoked outside a popup (env var `WORKSPACE_SWITCHER_INSIDE_POPUP` is unset), the script re-execs itself inside a centered tmux popup:
- Width: 60% (overridable via `$1`)
- Height: 50% (overridable via `$2`)
- Centered: `-xC -yC`
- Sets `WORKSPACE_SWITCHER_INSIDE_POPUP=1` to prevent recursion

### Session list
Each line displays:
- **Indicator**: `●` = current session, `◉` = attached (not current), `○` = detached
- **Display name**: session name with `wt-` prefix stripped for workspace sessions
- **Working directory**: basename of the active pane's `pane_current_path`
- **Window count**: e.g., "1 window" or "3 windows"

Skip any session named `popup` or `popover` (transient popup sessions).

### fzf interface
- Tab-separated data: raw session name in field 1 (hidden), display fields from field 2 onward
- `--with-nth=2..` to hide the raw name
- Header: `  Switch Session  ● current  ◉ attached  ○ detached`
- `--border=rounded --no-info --reverse`
- Prompt: `  ` (two spaces)
- Pointer: `▶`

### Switching
On selection, extract the raw session name from field 1. If it differs from the current session, run `tmux switch-client -t <target>`. If the user cancels fzf (empty selection), exit silently.

## 2. Keybinding

Add to `tmux/tmux.conf` in the Navigation section:

```
# Workspace switcher popup
bind w run-shell "workspace-switcher"
```

Use `workspace-switcher` (not a full path) since `~/bin` is in `$PATH` and `setup` symlinks scripts there.

---

## Implementation Plan

### Step 1: Create workspace-switcher script ✅

**Files:**
- `scripts/workspace-switcher` — New file

**Implement:**
1. Create `scripts/workspace-switcher` as a zsh script with `#!/usr/bin/env zsh` and `set -euo pipefail`
2. Accept optional width (`$1`, default `60%`) and height (`$2`, default `50%`) arguments
3. Implement the self-re-exec popup pattern: check `WORKSPACE_SWITCHER_INSIDE_POPUP`, if unset exec into `tmux popup -xC -yC -w"$width" -h"$height" -E` with the env var set
4. Get current session via `tmux display-message -p '#{session_name}'`
5. Build the session list: `tmux list-sessions -F '#{session_name}\t#{session_windows}\t#{session_attached}'`, sorted, skipping `popup` and `popover` sessions
6. For each session: determine indicator (`●`/`◉`/`○`), get pane CWD basename, strip `wt-` prefix for display, format with printf as tab-separated
7. Pipe list through fzf with the specified options
8. Extract target session name and `tmux switch-client -t` if different from current
9. Make the file executable

**Test:**
- `zsh -n scripts/workspace-switcher` passes syntax check
- `shellcheck scripts/workspace-switcher` passes (or has only zsh-specific exclusions)
- Manual: describe how to test (open tmux, create 2+ sessions, press `Prefix + w`)

**Verify:** Run `zsh -n scripts/workspace-switcher` and `shellcheck scripts/workspace-switcher`. Fix any issues.

**Review:** Check for: correct quoting, proper error handling if no sessions exist, graceful fzf cancellation, no hardcoded paths.

**Address feedback:** Fix all findings, re-verify.

### Step 2: Add tmux keybinding ✅

**Files:**
- `tmux/tmux.conf` — Add keybinding

**Implement:**
1. Add a workspace switcher binding in the Navigation section (after the window navigation bindings, before the Window management section):
   ```
   # Workspace switcher popup
   bind w run-shell "workspace-switcher"
   ```

**Test:**
- `grep 'bind w' tmux/tmux.conf` shows the new binding
- No duplicate `bind w` or `bind-key w` entries in the file

**Verify:** Visually confirm placement in the file is logical.

**Review:** Ensure no conflicts with existing bindings.

**Address feedback:** Fix any findings.

### Step 3: Run all checks

**Implement:**
1. Run `zsh -n` on all zsh scripts that changed
2. Run `shellcheck` on all shell scripts that changed
3. Run the full BATS test suite: `bats tests/`
4. Fix any failures

**Verify:** All checks pass clean.

### Step 4: Create commit

**Implement:**
1. Stage `scripts/workspace-switcher` and `tmux/tmux.conf`
2. Create a commit: `Add tmux workspace switcher popup (Prefix + w)`

**Verify:** `git log -1` shows the commit with both files.

---

## Conventions

- **Language:** zsh
- **Tests:** BATS framework; this feature is primarily a UI script so testing is limited to syntax/lint checks
- **Error messages:** No prefix needed (script runs inside a popup, errors are transient)
- **Exit codes:** 0=success or user cancelled, 1=error
