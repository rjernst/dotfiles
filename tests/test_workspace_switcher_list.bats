#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/workspace-switcher-list (grouped session/window list builder)
# Uses mock tmux since tmux server may not be available.

setup() {
  SCRIPT="${BATS_TEST_FILENAME%/*}/../scripts/workspace-switcher-list"

  # Create mock directory
  MOCK_DIR="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_DIR"
  export TMUX_CMD="$MOCK_DIR/tmux"

  # Collapse state file
  COLLAPSE_FILE="$BATS_TEST_TMPDIR/collapse-state"
  : > "$COLLAPSE_FILE"
}

# --- mock helpers ---

# Create a mock tmux that returns configured sessions and auto-generates windows.
# Input on stdin: lines of "name\twindows\tattached"
# Supports: has-session, list-sessions, list-windows, display-message
# Override per-session windows with set_session_windows.
create_mock_tmux() {
  local sessions_file="$BATS_TEST_TMPDIR/tmux-sessions"
  cat > "$sessions_file"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
case "\$1" in
  has-session)
    target=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in -t) target="\$2"; shift 2 ;; *) shift ;; esac
    done
    grep -q "^\${target}	" "$sessions_file" 2>/dev/null
    ;;
  list-sessions)
    cat "$sessions_file"
    ;;
  list-windows)
    target=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in -t) target="\$2"; shift 2 ;; *) shift ;; esac
    done
    if [[ -f "$BATS_TEST_TMPDIR/tmux-windows-\$target" ]]; then
      cat "$BATS_TEST_TMPDIR/tmux-windows-\$target"
    else
      # Auto-generate windows from session's window count
      count=\$(grep "^\${target}	" "$sessions_file" 2>/dev/null | cut -f2)
      count=\${count:-0}
      for ((i=0; i<count; i++)); do
        printf '%s\tshell\t/home/user/code/%s\n' "\$i" "\$target"
      done
    fi
    ;;
  display-message)
    target=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in -t) target="\$2"; shift 2 ;; -p) shift; break ;; *) shift ;; esac
    done
    echo "/home/user/code/\$target"
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Override window data for a specific session
# Usage: set_session_windows "session-name" <<'WINDOWS'
#   0\twinname\t/path
# WINDOWS
set_session_windows() {
  cat > "$BATS_TEST_TMPDIR/tmux-windows-$1"
}

# Mock tmux with a mix of all session types + custom windows
create_mock_tmux_mixed() {
  create_mock_tmux <<'SESSIONS'
agent-loops	2	0
main	3	1
scratch	1	0
wt-bugfix-oom	1	0
wt-feature-branch	2	0
SESSIONS

  set_session_windows "agent-loops" <<'WINDOWS'
0	elasticsearch	/home/user/code/elasticsearch
1	kibana	/home/user/code/kibana
WINDOWS

  set_session_windows "main" <<'WINDOWS'
0	shell	/home/user/code/main
1	vim	/home/user/code/dotfiles
2	htop	/home/user/code/main
WINDOWS
}

# Mock tmux with no sessions
create_mock_tmux_empty() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 1 ;;
  list-sessions)
    echo "no server running" >&2
    exit 1
    ;;
  list-windows) exit 1 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# --- group header tests ---

@test "outputs Main group header when main session exists" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Main"* ]]
}

@test "outputs Agent Loops group header when agent-loops session exists" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Agent Loops"* ]]
}

@test "outputs Worktrees group header" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Worktrees"* ]]
}

@test "outputs Other group header when non-mapped sessions exist" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Other"* ]]
}

@test "hides Main group when main session doesn't exist" {
  create_mock_tmux <<'SESSIONS'
agent-loops	1	0
wt-feature	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" != *"Main"* ]]
}

@test "hides Agent Loops group when agent-loops session doesn't exist" {
  create_mock_tmux <<'SESSIONS'
main	1	1
wt-feature	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" != *"Agent Loops"* ]]
}

@test "hides Other group when no non-mapped sessions exist" {
  create_mock_tmux <<'SESSIONS'
main	1	1
agent-loops	1	0
wt-feature	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" != *"Other"* ]]
}

@test "Worktrees shown even when empty" {
  create_mock_tmux <<'SESSIONS'
main	1	1
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Worktrees (0)"* ]]
}

# --- window entries for Main and Agent Loops ---

@test "shows windows from main session in Main group" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Main (3)"* ]]
  [[ "$output" == *"shell"* ]]
  [[ "$output" == *"vim"* ]]
  [[ "$output" == *"htop"* ]]
}

@test "shows windows from agent-loops session in Agent Loops group" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Agent Loops (2)"* ]]
  [[ "$output" == *"elasticsearch"* ]]
  [[ "$output" == *"kibana"* ]]
}

@test "main and agent-loops don't appear in Other" {
  create_mock_tmux <<'SESSIONS'
main	1	1
agent-loops	1	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" != *"Other"* ]]
}

# --- session entries (Worktrees / Other) ---

@test "categorizes wt- sessions into Worktrees" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Worktrees (2)"* ]]
}

@test "strips wt- prefix for display" {
  create_mock_tmux <<'SESSIONS'
wt-feature-branch	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"feature-branch"* ]]
}

@test "categorizes plain sessions into Other" {
  create_mock_tmux <<'SESSIONS'
scratch	1	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Other (1)"* ]]
}

# --- window / session labels ---

@test "shows singular window label for sessions" {
  create_mock_tmux <<'SESSIONS'
wt-feature	1	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"1 window"* ]]
}

@test "shows plural windows label for sessions" {
  create_mock_tmux <<'SESSIONS'
wt-feature	3	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"3 windows"* ]]
}

# --- indicators ---

@test "shows current indicator for current window" {
  create_mock_tmux <<'SESSIONS'
main	2	1
SESSIONS
  set_session_windows "main" <<'WINDOWS'
0	shell	/home/user
1	vim	/home/user
WINDOWS

  CURRENT_SESSION=main CURRENT_WINDOW=0 run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"●"*"shell"* ]]
}

@test "non-current windows in current session show ○" {
  create_mock_tmux <<'SESSIONS'
main	2	1
SESSIONS
  set_session_windows "main" <<'WINDOWS'
0	shell	/home/user
1	vim	/home/user
WINDOWS

  CURRENT_SESSION=main CURRENT_WINDOW=0 run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"○"*"vim"* ]]
}

@test "shows ○ for windows in other sessions" {
  create_mock_tmux <<'SESSIONS'
main	1	1
agent-loops	1	0
SESSIONS

  CURRENT_SESSION=main CURRENT_WINDOW=0 run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"○"* ]]
}

@test "shows current indicator for current session (worktree)" {
  create_mock_tmux <<'SESSIONS'
wt-feature	1	0
SESSIONS

  CURRENT_SESSION=wt-feature run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"●"* ]]
}

@test "non-current sessions show ○ regardless of attached state" {
  create_mock_tmux <<'SESSIONS'
wt-feature	2	1
wt-other	1	0
SESSIONS

  CURRENT_SESSION=wt-other run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  # wt-feature is attached but should still show ○, not ◉
  [[ "$output" != *"◉"* ]]
}

# --- popup filtering ---

@test "skips popup sessions" {
  create_mock_tmux <<'SESSIONS'
main	1	1
popup	1	0
wt-feature	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  local session_lines
  session_lines=$(echo "$output" | grep -v "GROUP:" | grep -v "^$" || true)
  [[ "$session_lines" != *"popup"* ]]
}

@test "skips popover sessions" {
  create_mock_tmux <<'SESSIONS'
main	1	1
popover	1	0
wt-feature	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  local session_lines
  session_lines=$(echo "$output" | grep -v "GROUP:" | grep -v "^$" || true)
  [[ "$session_lines" != *"popover"* ]]
}

# --- collapse state ---

@test "expanded groups show down arrow" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"▾"* ]]
}

@test "collapsed group shows right arrow" {
  create_mock_tmux_mixed
  echo "agent-loops" > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"▸"* ]]
}

@test "collapsed group hides entries" {
  create_mock_tmux <<'SESSIONS'
agent-loops	2	0
SESSIONS
  set_session_windows "agent-loops" <<'WINDOWS'
0	elasticsearch	/home/user/code/elasticsearch
1	kibana	/home/user/code/kibana
WINDOWS
  echo "agent-loops" > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  # Header shows count
  [[ "$output" == *"Agent Loops (2)"* ]]
  # But individual windows should not appear
  [[ "$output" != *"elasticsearch"* ]]
  [[ "$output" != *"kibana"* ]]
}

@test "only specified group is collapsed" {
  create_mock_tmux_mixed
  echo "agent-loops" > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  # Worktrees should still be expanded and show sessions
  [[ "$output" == *"feature-branch"* ]]
}

# --- group header format ---

@test "group headers have GROUP: prefix for fzf extraction" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"GROUP:main"* ]]
  [[ "$output" == *"GROUP:agent-loops"* ]]
  [[ "$output" == *"GROUP:worktrees"* ]]
  [[ "$output" == *"GROUP:other"* ]]
}

# --- group ordering ---

@test "Main appears before Agent Loops in output" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  local main_pos al_pos
  main_pos=$(echo "$output" | grep -n "Main" | head -1 | cut -d: -f1)
  al_pos=$(echo "$output" | grep -n "Agent Loops" | head -1 | cut -d: -f1)
  [ "$main_pos" -lt "$al_pos" ]
}

@test "Agent Loops appears before Worktrees in output" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  local al_pos wt_pos
  al_pos=$(echo "$output" | grep -n "Agent Loops" | head -1 | cut -d: -f1)
  wt_pos=$(echo "$output" | grep -n "Worktrees" | head -1 | cut -d: -f1)
  [ "$al_pos" -lt "$wt_pos" ]
}

@test "Worktrees appears before Other in output" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  local wt_pos other_pos
  wt_pos=$(echo "$output" | grep -n "Worktrees" | head -1 | cut -d: -f1)
  other_pos=$(echo "$output" | grep -n "Other" | head -1 | cut -d: -f1)
  [ "$wt_pos" -lt "$other_pos" ]
}

# --- error handling ---

@test "fails with usage message when no arguments" {
  run zsh "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"usage:"* ]]
}

@test "handles empty session list gracefully" {
  create_mock_tmux_empty

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Worktrees (0)"* ]]
  # Main and Agent Loops not shown when sessions don't exist
  [[ "$output" != *"Main"* ]]
  [[ "$output" != *"Agent Loops"* ]]
}

@test "handles missing collapse state file" {
  create_mock_tmux_mixed
  rm -f "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$BATS_TEST_TMPDIR/nonexistent-file"
  [ "$status" -eq 0 ]
  # Should treat all groups as expanded
  [[ "$output" == *"▾"* ]]
}

# --- toggle collapse state ---

@test "toggle collapses an expanded group" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"agent-loops"* ]]
}

@test "toggle expands a collapsed group" {
  create_mock_tmux_mixed
  echo "agent-loops" > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" != *"agent-loops"* ]]
}

@test "toggle only affects the specified group" {
  create_mock_tmux_mixed
  printf 'agent-loops\nworktrees\n' > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" != *"agent-loops"* ]]
  [[ "$output" == *"worktrees"* ]]
}

@test "toggle outputs updated list after toggling" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  [[ "$output" == *"▸"*"Agent Loops"* ]]
  [[ "$output" == *"▾"*"Worktrees"* ]]
}

@test "toggle with missing state file creates the file" {
  create_mock_tmux_mixed
  rm -f "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" worktrees
  [ "$status" -eq 0 ]
  [ -f "$COLLAPSE_FILE" ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"worktrees"* ]]
}

@test "toggle is idempotent when toggled twice" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" != *"agent-loops"* ]]
}

# --- resolve group key from raw field values ---

@test "toggle resolves GROUP: prefixed value to group key" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" "GROUP:agent-loops"
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"agent-loops"* ]]
}

@test "toggle resolves window target to agent-loops group" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" "agent-loops:0"
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"agent-loops"* ]]
}

@test "toggle resolves window target to main group" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" "main:2"
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"main"* ]]
}

@test "toggle resolves wt- session name to worktrees group" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" "wt-feature-branch"
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"worktrees"* ]]
}

@test "toggle resolves plain session name to other group" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" "scratch"
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"other"* ]]
}

# --- numbered shortcuts ---

@test "Main windows have 0-based m-prefixed numbers" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"m0"*"shell"* ]]
  [[ "$output" == *"m1"*"vim"* ]]
  [[ "$output" == *"m2"*"htop"* ]]
}

@test "Agent Loops windows have 0-based a-prefixed numbers" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"a0"*"elasticsearch"* ]]
  [[ "$output" == *"a1"*"kibana"* ]]
}

@test "Worktree sessions have 0-based w-prefixed numbers" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"w0"* ]]
  [[ "$output" == *"w1"* ]]
}

@test "Other sessions have no number prefix" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  # scratch should appear without a letter prefix
  [[ "$output" == *"○"*"scratch"* ]]
  # but not with a letter+number prefix
  [[ "$output" != *"o1"*"scratch"* ]]
}
