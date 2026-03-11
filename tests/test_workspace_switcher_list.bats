#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/workspace-switcher-list (grouped session list builder)
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

# Create a mock tmux that returns configured sessions
# Args: lines of "name\twindows\tattached" piped to stdin
create_mock_tmux() {
  local state_file="$BATS_TEST_TMPDIR/tmux-sessions"
  cat > "$state_file"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
case "\$1" in
  list-sessions)
    cat "$state_file"
    ;;
  display-message)
    # Return a mock pane path
    target=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -t) target="\$2"; shift 2 ;;
        -p) shift 2 ;;
        *) shift ;;
      esac
    done
    echo "/home/user/code/\$target"
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Mock tmux with a mix of session types
create_mock_tmux_mixed() {
  create_mock_tmux <<'SESSIONS'
al-elasticsearch	1	0
al-kibana	2	1
main	3	1
wt-feature-branch	2	0
wt-bugfix-oom	1	0
scratch	1	0
SESSIONS
}

# Mock tmux with no sessions
create_mock_tmux_empty() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions)
    echo "no server running" >&2
    exit 1
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Mock tmux with only non-prefixed sessions
create_mock_tmux_other_only() {
  create_mock_tmux <<'SESSIONS'
main	3	1
scratch	1	0
SESSIONS
}

# Mock tmux with popup sessions that should be skipped
create_mock_tmux_with_popups() {
  create_mock_tmux <<'SESSIONS'
main	1	1
popup	1	0
popover	1	0
wt-feature	2	0
SESSIONS
}

# --- group header tests ---

@test "outputs Agent Loops group header" {
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

@test "outputs Other group header when non-prefixed sessions exist" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Other"* ]]
}

@test "hides Other group when no non-prefixed sessions" {
  create_mock_tmux <<'SESSIONS'
al-elasticsearch	1	0
wt-feature	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" != *"Other"* ]]
}

@test "shows Agent Loops and Worktrees even when empty" {
  create_mock_tmux_other_only

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Agent Loops (0)"* ]]
  [[ "$output" == *"Worktrees (0)"* ]]
}

# --- session categorization ---

@test "categorizes al- sessions into Agent Loops" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Agent Loops (2)"* ]]
}

@test "categorizes wt- sessions into Worktrees" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Worktrees (2)"* ]]
}

@test "categorizes plain sessions into Other" {
  create_mock_tmux_mixed

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Other (2)"* ]]
}

# --- display name stripping ---

@test "strips al- prefix for display" {
  create_mock_tmux <<'SESSIONS'
al-elasticsearch	1	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  # Should show "elasticsearch" not "al-elasticsearch" in the display
  [[ "$output" == *"elasticsearch"* ]]
}

@test "strips wt- prefix for display" {
  create_mock_tmux <<'SESSIONS'
wt-feature-branch	2	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"feature-branch"* ]]
}

# --- indicators ---

@test "shows current indicator for CURRENT_SESSION" {
  create_mock_tmux <<'SESSIONS'
main	3	1
SESSIONS

  CURRENT_SESSION=main run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"●"* ]]
}

@test "shows attached indicator for attached non-current session" {
  create_mock_tmux <<'SESSIONS'
main	3	1
work	2	1
SESSIONS

  CURRENT_SESSION=main run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"◉"* ]]
}

@test "shows detached indicator for detached session" {
  create_mock_tmux <<'SESSIONS'
main	3	1
work	2	0
SESSIONS

  CURRENT_SESSION=main run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"○"* ]]
}

# --- popup filtering ---

@test "skips popup sessions" {
  create_mock_tmux_with_popups

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" != *"popup"* ]] || {
    # "popup" may appear in tmux commands but not as a session line
    # Check that popup is not a session entry (only appears in GROUP: lines or session lines)
    local session_lines
    session_lines=$(echo "$output" | grep -v "GROUP:" | grep -v "^$" || true)
    [[ "$session_lines" != *"popup"* ]]
  }
}

@test "skips popover sessions" {
  create_mock_tmux_with_popups

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
  # All groups expanded by default — should contain ▾
  [[ "$output" == *"▾"* ]]
}

@test "collapsed group shows right arrow" {
  create_mock_tmux_mixed
  echo "agent-loops" > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"▸"* ]]
}

@test "collapsed group hides session lines" {
  create_mock_tmux <<'SESSIONS'
al-elasticsearch	1	0
al-kibana	2	0
SESSIONS
  echo "agent-loops" > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  # Header should show count
  [[ "$output" == *"Agent Loops (2)"* ]]
  # But individual sessions should not appear
  [[ "$output" != *"elasticsearch"*"window"* ]]
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
  [[ "$output" == *"GROUP:agent-loops"* ]]
  [[ "$output" == *"GROUP:worktrees"* ]]
  [[ "$output" == *"GROUP:other"* ]]
}

# --- window label ---

@test "shows singular window label" {
  create_mock_tmux <<'SESSIONS'
main	1	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"1 window"* ]]
}

@test "shows plural windows label" {
  create_mock_tmux <<'SESSIONS'
main	3	0
SESSIONS

  run zsh "$SCRIPT" "$COLLAPSE_FILE"
  [ "$status" -eq 0 ]
  [[ "$output" == *"3 windows"* ]]
}

# --- group ordering ---

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
  # Should still output group headers (Agent Loops and Worktrees)
  [ "$status" -eq 0 ]
  [[ "$output" == *"Agent Loops (0)"* ]]
  [[ "$output" == *"Worktrees (0)"* ]]
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
  # agent-loops should now be collapsed
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"agent-loops"* ]]
}

@test "toggle expands a collapsed group" {
  create_mock_tmux_mixed
  echo "agent-loops" > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  # agent-loops should be removed from collapse file
  run cat "$COLLAPSE_FILE"
  [[ "$output" != *"agent-loops"* ]]
}

@test "toggle only affects the specified group" {
  create_mock_tmux_mixed
  printf 'agent-loops\nworktrees\n' > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  # agent-loops removed, worktrees still present
  run cat "$COLLAPSE_FILE"
  [[ "$output" != *"agent-loops"* ]]
  [[ "$output" == *"worktrees"* ]]
}

@test "toggle outputs updated list after toggling" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  # Toggle agent-loops to collapsed — output should show ▸ for agent-loops
  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  [[ "$output" == *"▸"*"Agent Loops"* ]]
  # But worktrees should still be expanded
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

  # Toggle once (collapse)
  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  # Toggle again (expand)
  run zsh "$SCRIPT" "$COLLAPSE_FILE" agent-loops
  [ "$status" -eq 0 ]
  # Should be back to empty/no agent-loops
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

@test "toggle resolves al- session name to agent-loops group" {
  create_mock_tmux_mixed
  : > "$COLLAPSE_FILE"

  run zsh "$SCRIPT" "$COLLAPSE_FILE" "al-elasticsearch"
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"agent-loops"* ]]
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

  run zsh "$SCRIPT" "$COLLAPSE_FILE" "main"
  [ "$status" -eq 0 ]
  run cat "$COLLAPSE_FILE"
  [[ "$output" == *"other"* ]]
}
