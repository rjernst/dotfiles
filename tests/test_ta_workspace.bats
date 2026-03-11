#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/ta-workspace (tmux workspace session manager)
# Uses mock tmux and ta-wt scripts since tmux server may not be available.

setup() {
  TA="${BATS_TEST_FILENAME%/*}/../scripts/ta"
  TA_WORKSPACE="${BATS_TEST_FILENAME%/*}/../scripts/ta-workspace"

  # Create mock directory
  MOCK_DIR="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_DIR"
  export TMUX_CMD="$MOCK_DIR/tmux"
  export TA_WT_CMD="$MOCK_DIR/ta-wt"

  # Unset TMUX to simulate being outside tmux by default
  unset TMUX
}

# Detect available shell (zsh preferred, bash fallback)
if command -v zsh &>/dev/null; then
  SHELL_CMD=zsh
else
  SHELL_CMD=bash
fi

# --- mock helpers ---

# Mock ta-wt that returns worktree JSON
create_mock_ta_wt() {
  cat > "$TA_WT_CMD" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "list" && "$2" == "--json" ]]; then
  cat <<'JSON'
[
  {"branch": "main", "status": "current", "ahead": 0, "behind": 0, "path": "/home/user/code/elasticsearch"},
  {"branch": "feature/fix-thing", "status": "clean", "ahead": 3, "behind": 1, "path": "/home/user/code/es-fix-thing"},
  {"branch": "bugfix/oom", "status": "dirty(2M)", "ahead": 1, "behind": 0, "path": "/home/user/code/es-oom"}
]
JSON
else
  echo "ta-wt mock: unsupported args: $*" >&2
  exit 1
fi
SCRIPT
  chmod +x "$TA_WT_CMD"
}

# Mock ta-wt with no worktrees (just main)
create_mock_ta_wt_empty() {
  cat > "$TA_WT_CMD" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "list" && "$2" == "--json" ]]; then
  echo '[{"branch": "main", "status": "current", "ahead": 0, "behind": 0, "path": "/home/user/code/elasticsearch"}]'
else
  echo "ta-wt mock: unsupported args: $*" >&2
  exit 1
fi
SCRIPT
  chmod +x "$TA_WT_CMD"
}

# Mock tmux with no server
create_mock_tmux_no_server() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
echo "no server running on /tmp/tmux-1000/default" >&2
exit 1
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Mock tmux with sessions (tracks created sessions via a state file)
create_mock_tmux_with_sessions() {
  local state_file="$BATS_TEST_TMPDIR/tmux-state"
  # Pre-populate with existing sessions
  cat > "$state_file" <<'STATE'
main	3	1
wt-feature-fix-thing	2	0
work	1	0
STATE

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
STATE_FILE="$state_file"
case "\$1" in
  list-sessions)
    if [[ ! -s "\$STATE_FILE" ]]; then
      echo "no server running" >&2
      exit 1
    fi
    while IFS=\$'\t' read -r name windows attached; do
      printf "%s\t%s\t%s\n" "\$name" "\$windows" "\$attached"
    done < "\$STATE_FILE"
    ;;
  has-session)
    target=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -t) target="\$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    grep -q "^\${target}\b" "\$STATE_FILE" 2>/dev/null
    ;;
  new-session)
    sess="" dir=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -d) shift ;;
        -s) sess="\$2"; shift 2 ;;
        -c) dir="\$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    printf "%s\t1\t0\n" "\$sess" >> "\$STATE_FILE"
    ;;
  display-message)
    target="" fmt=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -t) target="\$2"; shift 2 ;;
        -p) fmt="\$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ "\$fmt" == *"pane_current_path"* ]]; then
      echo "/home/user/code/mock-dir"
    elif [[ "\$fmt" == *"session_attached"* ]]; then
      name="\${target%:}"
      attached="\$(grep "^\${name}\b" "\$STATE_FILE" 2>/dev/null | cut -f3)"
      echo "\${attached:-0}"
    fi
    ;;
  kill-session)
    target=""
    shift
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -t) target="\$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ -n "\$target" ]]; then
      tmp="\$(mktemp)"
      grep -v "^\${target}\b" "\$STATE_FILE" > "\$tmp" 2>/dev/null || true
      mv "\$tmp" "\$STATE_FILE"
    fi
    ;;
  switch-client|attach-session)
    exit 0
    ;;
  send-keys|rename-window|new-window|select-window)
    exit 0
    ;;
  show-environment)
    echo "DOTFILES_AGENT_WINDOWS: unknown variable" >&2
    exit 1
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Mock tmux with no sessions (empty server)
create_mock_tmux_empty() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions)
    echo "no server running" >&2
    exit 1
    ;;
  has-session)
    exit 1
    ;;
  new-session)
    exit 0
    ;;
  send-keys)
    exit 0
    ;;
  show-environment)
    exit 1
    ;;
  switch-client|attach-session)
    exit 0
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Mock tmux that logs all calls and supports agent window testing
create_mock_tmux_logging() {
  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  local agent_windows="${1:-off}"
  : > "$call_log"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
CALL_LOG="$call_log"
echo "\$@" >> "\$CALL_LOG"
case "\$1" in
  list-sessions)
    echo "no server running" >&2
    exit 1
    ;;
  has-session)
    exit 1
    ;;
  new-session|new-window|rename-window|select-window|send-keys)
    exit 0
    ;;
  show-environment)
    if [[ "$agent_windows" == "on" ]]; then
      echo "DOTFILES_AGENT_WINDOWS=on"
    else
      echo "DOTFILES_AGENT_WINDOWS: unknown variable" >&2
      exit 1
    fi
    ;;
  switch-client|attach-session)
    exit 0
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}


# --- sanitization tests ---

@test "sanitize: feature/foo becomes wt-feature-foo" {
  create_mock_tmux_no_server
  create_mock_ta_wt
  # We test sanitization indirectly through create's "session already exists" message
  # or by calling the function. Since we can't call internal functions directly,
  # test through create behavior.

  # Use a simpler approach: run the script and look for the session name in output
  create_mock_tmux_empty
  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing
  [ "$status" -eq 0 ]
  [[ "$output" == *"wt-feature-fix-thing"* ]]
}

@test "sanitize: multiple slashes handled" {
  create_mock_tmux_empty
  create_mock_ta_wt
  # Add a branch with multiple slashes to the mock
  cat > "$TA_WT_CMD" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "list" && "$2" == "--json" ]]; then
  echo '[{"branch": "fix/area/detail", "status": "clean", "ahead": 0, "behind": 0, "path": "/tmp/wt"}]'
fi
SCRIPT
  chmod +x "$TA_WT_CMD"

  run "$SHELL_CMD" "$TA_WORKSPACE" create fix/area/detail
  [ "$status" -eq 0 ]
  [[ "$output" == *"wt-fix-area-detail"* ]]
}

@test "sanitize: special chars replaced with dashes" {
  create_mock_tmux_empty
  cat > "$TA_WT_CMD" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "list" && "$2" == "--json" ]]; then
  echo '[{"branch": "fix@thing#1", "status": "clean", "ahead": 0, "behind": 0, "path": "/tmp/wt"}]'
fi
SCRIPT
  chmod +x "$TA_WT_CMD"

  run "$SHELL_CMD" "$TA_WORKSPACE" create 'fix@thing#1'
  [ "$status" -eq 0 ]
  [[ "$output" == *"wt-fix-thing-1"* ]]
}

@test "sanitize: leading/trailing dashes removed" {
  create_mock_tmux_empty
  cat > "$TA_WT_CMD" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "list" && "$2" == "--json" ]]; then
  echo '[{"branch": "/leading-slash", "status": "clean", "ahead": 0, "behind": 0, "path": "/tmp/wt"}]'
fi
SCRIPT
  chmod +x "$TA_WT_CMD"

  run "$SHELL_CMD" "$TA_WORKSPACE" create '/leading-slash'
  [ "$status" -eq 0 ]
  [[ "$output" == *"wt-leading-slash"* ]]
  # Should NOT have wt--leading
  [[ "$output" != *"wt--"* ]]
}

# --- dispatcher tests ---

@test "ta workspace dispatches to ta-workspace" {
  # ta dispatcher uses zsh-specific ${0:A:h}, requires zsh
  command -v zsh &>/dev/null || skip "zsh not available"
  run zsh "$TA" workspace
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: ta workspace"* ]]
}

@test "ta-workspace with no args shows usage" {
  run "$SHELL_CMD" "$TA_WORKSPACE"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "ta-workspace unknown command fails" {
  run "$SHELL_CMD" "$TA_WORKSPACE" bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown workspace command"* ]]
}

# --- create tests ---

@test "create: no branch shows usage" {
  run "$SHELL_CMD" "$TA_WORKSPACE" create
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "create: creates session for branch with worktree" {
  create_mock_tmux_empty
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing
  [ "$status" -eq 0 ]
  [[ "$output" == *"created session"* ]]
  [[ "$output" == *"wt-feature-fix-thing"* ]]
  [[ "$output" == *"/home/user/code/es-fix-thing"* ]]
}

@test "create: is idempotent (second call prints message, exits 0)" {
  create_mock_tmux_with_sessions
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing
  [ "$status" -eq 0 ]
  [[ "$output" == *"already exists"* ]]
}

@test "create: fails if branch has no worktree" {
  create_mock_tmux_empty
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create nonexistent/branch
  [ "$status" -eq 1 ]
  [[ "$output" == *"no worktree found"* ]]
}

@test "create: with --cmd sends command to session" {
  # Use a mock that tracks send-keys calls
  create_mock_ta_wt
  local state_file="$BATS_TEST_TMPDIR/send-keys-log"
  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
case "\$1" in
  list-sessions) echo "no server running" >&2; exit 1 ;;
  has-session) exit 1 ;;
  new-session) exit 0 ;;
  send-keys)
    echo "\$@" >> "$state_file"
    exit 0
    ;;
  show-environment) exit 1 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --cmd "git status"
  [ "$status" -eq 0 ]
  [[ "$output" == *"created session"* ]]

  # Verify send-keys was called with the command
  [ -f "$state_file" ]
  grep -q "git status" "$state_file"
}

# --- list tests ---

@test "list: with no tmux server shows empty message" {
  create_mock_tmux_no_server

  run "$SHELL_CMD" "$TA_WORKSPACE" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"no tmux sessions"* ]]
}

@test "list: only shows wt-* sessions" {
  create_mock_tmux_with_sessions

  run "$SHELL_CMD" "$TA_WORKSPACE" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"wt-feature-fix-thing"* ]]
  # Should NOT show non-wt sessions
  [[ "$output" != *"work	"* ]]
  # 'main' should not appear as a session row (only in header)
  local line_count
  line_count=$(echo "$output" | grep -c "wt-" || true)
  [ "$line_count" -eq 1 ]
}

@test "list: shows header with SESSION columns" {
  create_mock_tmux_with_sessions

  run "$SHELL_CMD" "$TA_WORKSPACE" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"SESSION"* ]]
  [[ "$output" == *"ATTACHED"* ]]
  [[ "$output" == *"WINDOWS"* ]]
  [[ "$output" == *"CWD"* ]]
}

@test "list: with no wt-* sessions prints empty message" {
  # Mock with sessions but none starting with wt-
  local state_file="$BATS_TEST_TMPDIR/tmux-state"
  cat > "$state_file" <<'STATE'
main	3	1
work	1	0
STATE
  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
case "\$1" in
  list-sessions)
    while IFS=\$'\t' read -r name windows attached; do
      printf "%s\t%s\t%s\n" "\$name" "\$windows" "\$attached"
    done < "$state_file"
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  run "$SHELL_CMD" "$TA_WORKSPACE" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"no workspace sessions"* ]]
}

# --- attach tests ---

@test "attach: no branch shows usage" {
  run "$SHELL_CMD" "$TA_WORKSPACE" attach
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "attach: auto-creates if session doesn't exist" {
  create_mock_tmux_empty
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" attach feature/fix-thing
  [ "$status" -eq 0 ]
  [[ "$output" == *"created session"* ]]
}

@test "attach: attaches to existing session (outside tmux)" {
  create_mock_tmux_with_sessions
  create_mock_ta_wt

  # Ensure TMUX is unset (outside tmux)
  unset TMUX
  run "$SHELL_CMD" "$TA_WORKSPACE" attach feature/fix-thing
  [ "$status" -eq 0 ]
  # Should NOT create (session already exists)
  [[ "$output" != *"created session"* ]]
}

@test "attach: uses switch-client inside tmux" {
  create_mock_ta_wt
  local call_log="$BATS_TEST_TMPDIR/tmux-calls"
  : > "$call_log"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
echo "\$1 \$*" >> "$call_log"
case "\$1" in
  list-sessions) printf "wt-feature-fix-thing\t2\t0\n" ;;
  has-session) exit 0 ;;
  switch-client) exit 0 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  # Simulate being inside tmux
  TMUX="/tmp/tmux-1000/default,12345,0" run "$SHELL_CMD" "$TA_WORKSPACE" attach feature/fix-thing
  [ "$status" -eq 0 ]

  # Verify switch-client was called (not attach-session)
  grep -q "switch-client" "$call_log"
  ! grep -q "attach-session" "$call_log"
}

# --- kill tests ---

@test "kill: no branch shows usage" {
  run "$SHELL_CMD" "$TA_WORKSPACE" kill
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "kill: nonexistent session returns error" {
  create_mock_tmux_with_sessions

  run "$SHELL_CMD" "$TA_WORKSPACE" kill nonexistent/branch
  [ "$status" -eq 1 ]
  [[ "$output" == *"not found"* ]]
}

@test "kill: kills existing session" {
  create_mock_tmux_with_sessions

  run "$SHELL_CMD" "$TA_WORKSPACE" kill feature/fix-thing
  [ "$status" -eq 0 ]
  [[ "$output" == *"killed session"* ]]
  [[ "$output" == *"wt-feature-fix-thing"* ]]
}

@test "kill: kills unattached session without prompt" {
  create_mock_tmux_with_sessions

  # wt-feature-fix-thing has attached=0 in the mock state
  run "$SHELL_CMD" "$TA_WORKSPACE" kill feature/fix-thing
  [ "$status" -eq 0 ]
  [[ "$output" == *"killed"* ]]
}

# --- multi-window workspace tests ---

@test "create: renames window 0 to shell" {
  create_mock_tmux_logging off
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  grep -q "rename-window.*shell" "$call_log"
}

@test "create: without DOTFILES_AGENT_WINDOWS, session has no extra windows" {
  create_mock_tmux_logging off
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  # Should not create additional windows
  ! grep -q "new-window" "$call_log"
}

@test "create: with DOTFILES_AGENT_WINDOWS=on, creates agent and agent-loop windows" {
  create_mock_tmux_logging on
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  # Should create agent window
  grep -q "new-window.*-n agent " "$call_log" || grep -q "new-window.*-n agent$" "$call_log"
  # Should create agent-loop window
  grep -q "new-window.*-n agent-loop" "$call_log"
  # Should send claude to agent window
  grep -q "send-keys.*:agent claude" "$call_log"
  # Should send ralph to agent-loop window
  grep -q "send-keys.*:agent-loop ralph" "$call_log"
  # Should select window 0
  grep -q "select-window.*:0" "$call_log"
}

@test "create: --cmd flag applies to shell window only with agent windows" {
  create_mock_tmux_logging on
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --cmd "git status"
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  # The --cmd sends to the shell window
  grep -q "send-keys.*:shell git status" "$call_log"
  # Agent windows still get their commands
  grep -q "send-keys.*:agent claude" "$call_log"
  grep -q "send-keys.*:agent-loop ralph" "$call_log"
}
