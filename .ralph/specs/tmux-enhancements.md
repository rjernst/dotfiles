branch: tmux-enhancements

# Spec: Tmux Enhancements

## Overview
Improve the tmux experience with four enhancements: (1) automatic window naming that shows `repo [branch]`, (2) split panes that preserve the working directory, (3) a help popup showing a keybinding cheatsheet, and (4) multi-window workspace sessions with dedicated agent and agent-loop windows.

## Architecture

```
tmux/tmux.conf                  # Updated: split bindings, auto-rename, help popup binding, source local config
tmux/tmux.conf.local.example    # New: example local config showing agent_windows setting
tmux/cheatsheet.txt             # New: static keybinding cheatsheet (initially generated from tmux.conf)
scripts/tmux-window-name        # New: helper script outputting "repo [branch]" for tmux auto-rename
scripts/ta-workspace            # Updated: create 3-window sessions when configured
```

Data flow for auto-rename:
```
tmux automatic-rename-format → runs tmux-window-name → git rev-parse for repo + branch → "repo [branch]"
```

Data flow for workspace creation:
```
ta-workspace create <branch>
  → creates session with window 0: "shell"
  → if agent_windows enabled (~/.tmux.conf.local):
      → creates window 1: "agent" (sends "claude" via send-keys)
      → creates window 2: "agent-loop" (sends "ralph" via send-keys)
  → selects window 0 (shell) as active
```

---

## 1. Same-Directory Split Panes

Add `-c "#{pane_current_path}"` to the existing `|` and `-` split bindings in `tmux/tmux.conf` so new panes open in the same directory as the current pane.

**Current:**
```
bind | split-window -h
bind - split-window -v
```

**Target:**
```
bind | split-window -h -c "#{pane_current_path}"
bind - split-window -v -c "#{pane_current_path}"
```

## 2. Automatic Window Naming

Create a helper script `scripts/tmux-window-name` that outputs a formatted name for the current pane's working directory:
- Get git repo root via `git rev-parse --show-toplevel`, extract basename as repo name
- Get current branch via `git rev-parse --abbrev-ref HEAD`
- If branch is `main` or `master`, output just `repo`
- Otherwise strip common prefixes (`feature/`, `bugfix/`, `hotfix/`) and output `repo [branch]`
- If not in a git repo, output the basename of the working directory
- The script must accept a path argument (tmux passes `#{pane_current_path}`)
- Must be fast (<50ms) since tmux runs it frequently

Configure tmux to use it:
```
set-option -g automatic-rename on
set-option -g automatic-rename-format '#(tmux-window-name "#{pane_current_path}")'
```

Note: `tmux-window-name` will be auto-symlinked to `~/bin` by `setup` since it's in `scripts/`.

## 3. Help Popup

Create a static cheatsheet file at `tmux/cheatsheet.txt` containing categorized keybinding reference. Generate the initial content by reading the current `tmux/tmux.conf` bindings, organized into categories:
- Navigation (panes, windows, sessions)
- Splits & Layout
- Copy Mode
- Workspace Switcher
- Other

Bind `prefix + h` to display the cheatsheet in a tmux popup:
```
bind h display-popup -w 70% -h 70% -E "cat ~/.dotfiles/tmux/cheatsheet.txt | less -R"
```

The cheatsheet is manually maintained after initial generation. It should be clearly formatted with section headers, aligned columns, and brief descriptions.

## 4. Multi-Window Workspace Sessions

### Configuration

Add to the end of `tmux/tmux.conf`:
```
# Source local config if it exists
if-shell "[ -f ~/.tmux.conf.local ]" "source-file ~/.tmux.conf.local"
```

Create `tmux/tmux.conf.local.example` showing available settings:
```
# Set to "on" to create agent windows in workspace sessions
# set-environment -g DOTFILES_AGENT_WINDOWS "on"
```

The user copies this to `~/.tmux.conf.local` and uncomments the setting. This file is NOT symlinked or tracked — it's a local override.

### ta-workspace Changes

Modify `scripts/ta-workspace` `create` subcommand:

1. After creating the session (which becomes window 0), rename window 0 to "shell"
2. Check if agent windows are enabled: `tmux show-environment -g DOTFILES_AGENT_WINDOWS 2>/dev/null`
3. If enabled:
   - Create window 1 named "agent" in the same directory: `tmux new-window -t "$session" -n "agent" -c "$worktree_path"`
   - Send claude command: `tmux send-keys -t "$session:agent" "claude" Enter`
   - Create window 2 named "agent-loop" in the same directory: `tmux new-window -t "$session" -n "agent-loop" -c "$worktree_path"`
   - Send ralph command: `tmux send-keys -t "$session:agent-loop" "ralph" Enter`
   - Select window 0 (shell) so user lands there: `tmux select-window -t "$session:0"`
4. The `--cmd` flag (existing) should only apply to window 0 (shell), not override agent windows

### ta-ff Changes

No changes needed — `ta-ff` calls `ta-workspace create` internally, so it inherits the multi-window behavior automatically.

### Key Design Decision: send-keys not window commands

Agent windows use `send-keys` to type the command into a shell, NOT `tmux new-window "claude"`. This means:
- The shell persists after claude/ralph exits
- User can restart claude with the same session resume info
- Shell history and environment are preserved
- User can Ctrl+C and run other commands in the window

---

## Implementation Plan

### Step 1: Same-directory split panes [done]

**Files:**
- `tmux/tmux.conf` — Update split bindings

**Implement:**
1. Add `-c "#{pane_current_path}"` to both `bind |` and `bind -` split-window lines

**Test:**
- Visual verification: open tmux, `cd /tmp`, split with `|` and `-`, confirm new panes are in `/tmp`

**Verify:** `grep -c 'pane_current_path' tmux/tmux.conf` returns 2 (one per split binding).

**Review:** Ensure no other split bindings were missed.

**Address feedback:** Fix any findings.

### Step 2: Automatic window naming script [done]

**Files:**
- `scripts/tmux-window-name` — New helper script

**Implement:**
1. Create `scripts/tmux-window-name` (zsh script, executable)
2. Accept one positional argument: the pane's current path
3. `cd` to the path, run `git rev-parse --show-toplevel` and `git rev-parse --abbrev-ref HEAD`
4. If not in a git repo, output `basename "$1"` and exit
5. Extract repo name as `basename "$toplevel"`
6. If branch is `main` or `master`, output repo name only
7. Otherwise strip `feature/`, `bugfix/`, `hotfix/` prefixes from branch, output `repo [branch]`
8. Ensure the script is fast: no unnecessary subshells or network calls

**Test:**
- Create BATS test `tests/test_tmux_window_name.bats`
- Test cases: git repo on main (just repo name), git repo on feature branch (repo [branch]), git repo on branch with prefix (prefix stripped), non-git directory (basename), missing argument (empty or graceful fallback)
- Use temp git repos for isolation

**Verify:** Run `bats tests/test_tmux_window_name.bats`. Fix failures.

**Review:** Check edge cases: detached HEAD, bare repos, permission errors. Ensure performance is acceptable.

**Address feedback:** Fix findings, re-run tests.

### Step 3: Configure tmux auto-rename [done]

**Files:**
- `tmux/tmux.conf` — Add automatic-rename settings

**Implement:**
1. Add `set-option -g automatic-rename on`
2. Add `set-option -g automatic-rename-format '#(tmux-window-name "#{pane_current_path}")'`
3. Place these near the top of tmux.conf with the other set-option lines

**Test:**
- Verify the settings are present in tmux.conf
- Visual verification: reload tmux config, navigate to a git repo, confirm window name updates

**Verify:** `grep 'automatic-rename' tmux/tmux.conf` shows both lines.

**Review:** Ensure auto-rename doesn't conflict with manual window renames (tmux disables auto-rename when user manually names a window — this is default behavior and should be preserved).

**Address feedback:** Fix findings.

### Step 4: Generate help cheatsheet [done]

**Files:**
- `tmux/cheatsheet.txt` — New static cheatsheet file

**Implement:**
1. Read `tmux/tmux.conf` and extract all `bind` / `bind-key` lines
2. Organize into categories: Navigation, Splits & Layout, Copy Mode, Workspace, Other
3. Format as aligned columns: `Key` | `Action`
4. Include the prefix key (`C-Space`) at the top
5. Add section for common tmux built-in bindings that aren't in the config (e.g., `d` detach, `c` new window, `&` kill window, `,` rename window)
6. Write to `tmux/cheatsheet.txt`

**Test:**
- File exists and is non-empty
- Contains expected sections (Navigation, Splits, Copy Mode)
- Key descriptions are accurate

**Verify:** `cat tmux/cheatsheet.txt` shows a well-formatted cheatsheet.

**Review:** Ensure all current bindings are represented. Check formatting alignment.

**Address feedback:** Fix findings.

### Step 5: Help popup binding [done]

**Files:**
- `tmux/tmux.conf` — Add help popup binding

**Implement:**
1. Add binding: `bind h display-popup -w 70% -h 70% -E "cat $HOME/.dotfiles/tmux/cheatsheet.txt | less -R"`
2. Place near other popup bindings (workspace switcher, popover)

**Test:**
- Verify binding is present in tmux.conf
- Visual: reload config, press `prefix + h`, confirm cheatsheet appears in popup

**Verify:** `grep 'bind h' tmux/tmux.conf` shows the help binding.

**Review:** Ensure `h` doesn't conflict with existing bindings (currently used for pane navigation — check if `bind h` vs `bind -n C-h` conflict). Note: `bind h` requires prefix, `bind -n C-h` does not, so they should not conflict.

**Address feedback:** Fix findings.

### Step 6: Source local tmux config [done]

**Files:**
- `tmux/tmux.conf` — Add source-file for local config
- `tmux/tmux.conf.local.example` — New example file

**Implement:**
1. Add to end of `tmux/tmux.conf`: `if-shell "[ -f ~/.tmux.conf.local ]" "source-file ~/.tmux.conf.local"`
2. Create `tmux/tmux.conf.local.example` with commented-out `DOTFILES_AGENT_WINDOWS` setting and brief documentation

**Test:**
- Verify the if-shell line is at the end of tmux.conf
- Verify example file exists with correct content

**Verify:** Files contain expected content.

**Review:** Ensure the if-shell syntax is correct for tmux.

**Address feedback:** Fix findings.

### Step 7: Multi-window workspace sessions [done]

**Files:**
- `scripts/ta-workspace` — Update `create` subcommand for 3-window layout

**Implement:**
1. After the session is created, rename window 0 to "shell": `tmux rename-window -t "$session:0" "shell"`
2. Check for agent windows config: `agent_windows=$(tmux show-environment -g DOTFILES_AGENT_WINDOWS 2>/dev/null | cut -d= -f2)`
3. If `agent_windows` equals "on":
   - Create "agent" window: `tmux new-window -t "$session" -n "agent" -c "$worktree_path"`
   - Send command: `tmux send-keys -t "$session:agent" "claude" Enter`
   - Create "agent-loop" window: `tmux new-window -t "$session" -n "agent-loop" -c "$worktree_path"`
   - Send command: `tmux send-keys -t "$session:agent-loop" "ralph" Enter`
   - Select shell window: `tmux select-window -t "$session:0"`
4. Ensure existing `--cmd` flag only applies to window 0
5. If the `--cmd` flag is used alongside agent windows, it sends the custom command to window 0 instead of claude/ralph

**Test:**
- Update `tests/test_ta_workspace.bats`:
  - Test: without DOTFILES_AGENT_WINDOWS, session has 1 window
  - Test: with DOTFILES_AGENT_WINDOWS=on, session has 3 windows named "shell", "agent", "agent-loop"
  - Test: --cmd flag still works and applies to window 0 only
  - Test: window 0 is selected after creation
- Tests need a tmux server running (or mock tmux commands)

**Verify:** Run `bats tests/test_ta_workspace.bats`. Fix failures.

**Review:** Check that existing ta-workspace tests still pass. Ensure ta-ff inherits behavior correctly without changes.

**Address feedback:** Fix findings, re-run tests.

### Step 8: Run all checks [done]

**Implement:**
1. Run `shellcheck scripts/tmux-window-name scripts/ta-workspace`
2. Run `zsh -n scripts/tmux-window-name scripts/ta-workspace`
3. Run `bats tests/` (all test files)
4. Fix any failures

**Verify:** All checks pass clean.

### Step 9: Create commit

**Implement:**
1. Stage all changes and create a commit: "Add tmux enhancements: auto window naming, same-dir splits, help popup, multi-window workspaces"

**Verify:** `git log -1` shows the commit.

---

## Conventions

- **Language:** zsh for all scripts
- **Tests:** BATS framework with temp git repos for isolation
- **Error messages:** Prefix with script name (e.g., `tmux-window-name: error: ...`)
- **Exit codes:** 0=success, 1=runtime error, 2=usage error
