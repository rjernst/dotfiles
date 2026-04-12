#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/ta-workspace (tmux workspace session manager)
# Uses mock tmux and ta-wt scripts since tmux server may not be available.

setup() {
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
  switch-client|attach-session)
    exit 0
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Mock tmux that logs all calls
create_mock_tmux_logging() {
  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
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
  command -v ta >/dev/null 2>&1 || skip "ta binary not installed"
  run ta workspace
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

@test "attach: errors if session doesn't exist" {
  create_mock_tmux_empty
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" attach feature/fix-thing
  [ "$status" -eq 1 ]
  [[ "$output" == *"no workspace session for branch"* ]]
  [[ "$output" == *"ta workspace create"* ]]
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
  select-window) exit 0 ;;
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

@test "attach: --window selects window before switching" {
  create_mock_ta_wt
  local call_log="$BATS_TEST_TMPDIR/tmux-calls"
  : > "$call_log"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
echo "\$@" >> "$call_log"
case "\$1" in
  list-sessions) printf "wt-feature-fix-thing\t2\t0\n" ;;
  has-session) exit 0 ;;
  switch-client) exit 0 ;;
  select-window) exit 0 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  TMUX="/tmp/tmux-1000/default,12345,0" run "$SHELL_CMD" "$TA_WORKSPACE" attach feature/fix-thing --window agent
  [ "$status" -eq 0 ]

  # Verify select-window was called with the right target
  grep -q "select-window -t wt-feature-fix-thing:agent" "$call_log"
  # And switch-client was called after
  grep -q "switch-client" "$call_log"
}

@test "attach: --window with nonexistent window propagates error" {
  create_mock_ta_wt
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions) printf "wt-feature-fix-thing\t2\t0\n" ;;
  has-session) exit 0 ;;
  select-window) echo "can't find window" >&2; exit 1 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  TMUX="/tmp/tmux-1000/default,12345,0" run "$SHELL_CMD" "$TA_WORKSPACE" attach feature/fix-thing --window nonexistent
  [ "$status" -ne 0 ]
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

@test "kill: switches away from current session before killing" {
  local call_log="$BATS_TEST_TMPDIR/tmux-calls"
  : > "$call_log"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
echo "\$@" >> "$call_log"
case "\$1" in
  list-sessions) printf "wt-feature-fix-thing\t2\t1\n" ;;
  has-session) exit 0 ;;
  display-message)
    fmt=""
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -p) fmt="\$2"; shift 2 ;;
        -t) shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ "\$fmt" == *"session_name"* ]]; then
      echo "wt-feature-fix-thing"
    elif [[ "\$fmt" == *"session_attached"* ]]; then
      echo "0"
    fi
    ;;
  switch-client) exit 0 ;;
  kill-session) exit 0 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  # Simulate being inside tmux in the target session
  TMUX="/tmp/tmux-1000/default,12345,0" run "$SHELL_CMD" "$TA_WORKSPACE" kill feature/fix-thing
  [ "$status" -eq 0 ]

  # Verify switch-client -l was called before kill-session
  grep -q "switch-client -l" "$call_log"
  grep -q "kill-session" "$call_log"
}

@test "kill: does not switch-client when killing a different session" {
  local call_log="$BATS_TEST_TMPDIR/tmux-calls"
  : > "$call_log"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
echo "\$@" >> "$call_log"
case "\$1" in
  list-sessions) printf "wt-feature-fix-thing\t2\t0\n" ;;
  has-session) exit 0 ;;
  display-message)
    fmt=""
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -p) fmt="\$2"; shift 2 ;;
        -t) shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ "\$fmt" == *"session_name"* ]]; then
      echo "other-session"
    elif [[ "\$fmt" == *"session_attached"* ]]; then
      echo "0"
    fi
    ;;
  kill-session) exit 0 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  TMUX="/tmp/tmux-1000/default,12345,0" run "$SHELL_CMD" "$TA_WORKSPACE" kill feature/fix-thing
  [ "$status" -eq 0 ]

  # Should NOT call switch-client
  ! grep -q "switch-client" "$call_log"
  grep -q "kill-session" "$call_log"
}

@test "kill: does not switch-client when outside tmux" {
  local call_log="$BATS_TEST_TMPDIR/tmux-calls"
  : > "$call_log"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
echo "\$@" >> "$call_log"
case "\$1" in
  list-sessions) printf "wt-feature-fix-thing\t2\t0\n" ;;
  has-session) exit 0 ;;
  display-message)
    fmt=""
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -p) fmt="\$2"; shift 2 ;;
        -t) shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ "\$fmt" == *"session_attached"* ]]; then
      echo "0"
    fi
    ;;
  kill-session) exit 0 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  # TMUX is unset (outside tmux)
  unset TMUX
  run "$SHELL_CMD" "$TA_WORKSPACE" kill feature/fix-thing
  [ "$status" -eq 0 ]

  # Should NOT call switch-client
  ! grep -q "switch-client" "$call_log"
  grep -q "kill-session" "$call_log"
}

@test "kill: falls back to switch-client -n when -l fails" {
  local call_log="$BATS_TEST_TMPDIR/tmux-calls"
  : > "$call_log"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
echo "\$@" >> "$call_log"
case "\$1" in
  list-sessions) printf "wt-feature-fix-thing\t2\t1\n" ;;
  has-session) exit 0 ;;
  display-message)
    fmt=""
    while [[ \$# -gt 0 ]]; do
      case "\$1" in
        -p) fmt="\$2"; shift 2 ;;
        -t) shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ "\$fmt" == *"session_name"* ]]; then
      echo "wt-feature-fix-thing"
    elif [[ "\$fmt" == *"session_attached"* ]]; then
      echo "0"
    fi
    ;;
  switch-client)
    if [[ "\$2" == "-l" ]]; then
      exit 1
    fi
    exit 0
    ;;
  kill-session) exit 0 ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  TMUX="/tmp/tmux-1000/default,12345,0" run "$SHELL_CMD" "$TA_WORKSPACE" kill feature/fix-thing
  [ "$status" -eq 0 ]

  # Should have tried -l first, then -n
  grep -q "switch-client -l" "$call_log"
  grep -q "switch-client -n" "$call_log"
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

@test "create: without flags, session has shell window and no extra windows" {
  create_mock_tmux_logging
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  # Should rename window 0 to shell
  grep -q "rename-window.*shell" "$call_log"
  # Should not create additional windows
  ! grep -q "new-window" "$call_log"
}

@test "create: --cmd renames window 0 to review and sends command" {
  create_mock_tmux_logging
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --cmd "git status"
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  # Window 0 should be named "review", not "shell"
  grep -q "rename-window.*review" "$call_log"
  ! grep -q "rename-window.*shell" "$call_log"
  # Command sent to review window
  grep -q "send-keys.*:review git status" "$call_log"
}

# --- prune tests ---

# Mock tmux with sessions and configurable CWDs for prune testing
create_mock_tmux_for_prune() {
  local state_file="$BATS_TEST_TMPDIR/tmux-state"
  local cwd_file="$BATS_TEST_TMPDIR/tmux-cwds"

  # Sessions: mix of wt-* and non-wt-*
  cat > "$state_file" <<'STATE'
main	3	1
wt-feature-good	2	0
wt-feature-orphan	1	0
wt-another-orphan	1	0
work	1	0
STATE

  # CWDs for each session
  cat > "$cwd_file" <<STATE
wt-feature-good=$BATS_TEST_TMPDIR/worktrees/good
wt-feature-orphan=/path/that/does/not/exist
wt-another-orphan=$BATS_TEST_TMPDIR/not-a-worktree
STATE

  # Create the directory that exists but is NOT a worktree
  mkdir -p "$BATS_TEST_TMPDIR/not-a-worktree"
  # Create the directory that IS a worktree
  mkdir -p "$BATS_TEST_TMPDIR/worktrees/good"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
STATE_FILE="$state_file"
CWD_FILE="$cwd_file"
case "\$1" in
  list-sessions)
    if [[ "\$*" == *"#{session_name}"* ]]; then
      while IFS=\$'\t' read -r name windows attached; do
        echo "\$name"
      done < "\$STATE_FILE"
    else
      while IFS=\$'\t' read -r name windows attached; do
        printf "%s\t%s\t%s\n" "\$name" "\$windows" "\$attached"
      done < "\$STATE_FILE"
    fi
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
      name="\${target%:}"
      cwd="\$(grep "^\${name}=" "\$CWD_FILE" 2>/dev/null | cut -d= -f2)"
      echo "\${cwd:-/unknown}"
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
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Helper: mock git worktree list --porcelain output
mock_git_for_prune() {
  local git_mock="$MOCK_DIR/git"
  cat > "$git_mock" <<SCRIPT
#!/usr/bin/env bash
if [[ "\$1" == "worktree" && "\$2" == "list" && "\$3" == "--porcelain" ]]; then
  echo "worktree /home/user/code/elasticsearch"
  echo "HEAD abc123"
  echo "branch refs/heads/main"
  echo ""
  echo "worktree $BATS_TEST_TMPDIR/worktrees/good"
  echo "HEAD def456"
  echo "branch refs/heads/feature/good"
  echo ""
else
  # Fall through to real git for other commands
  /usr/bin/git "\$@"
fi
SCRIPT
  chmod +x "$git_mock"
  export PATH="$MOCK_DIR:$PATH"
}

@test "prune: fails outside a git repository" {
  local git_mock="$MOCK_DIR/git"
  cat > "$git_mock" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "rev-parse" && "$2" == "--git-dir" ]]; then
  echo "fatal: not a git repository" >&2
  exit 128
fi
SCRIPT
  chmod +x "$git_mock"
  export PATH="$MOCK_DIR:$PATH"

  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 1 ]
  [[ "$output" == *"must be run from within a git repository"* ]]
}

@test "prune: no tmux server shows no orphaned sessions" {
  create_mock_tmux_no_server

  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no orphaned sessions"* ]]
}

@test "prune: no wt-* sessions shows no orphaned sessions" {
  local state_file="$BATS_TEST_TMPDIR/tmux-state"
  cat > "$state_file" <<'STATE'
main	3	1
work	1	0
STATE
  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
case "\$1" in
  list-sessions)
    if [[ "\$*" == *"#{session_name}"* ]]; then
      while IFS=\$'\t' read -r name windows attached; do
        echo "\$name"
      done < "$state_file"
    else
      cat "$state_file"
    fi
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  mock_git_for_prune
  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no orphaned sessions"* ]]
}

@test "prune: dry-run lists orphaned sessions" {
  create_mock_tmux_for_prune
  mock_git_for_prune

  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"Orphaned workspace sessions"* ]]
  [[ "$output" == *"wt-feature-orphan"* ]]
  [[ "$output" == *"wt-another-orphan"* ]]
  # Good session should NOT appear
  [[ "$output" != *"wt-feature-good"* ]]
  # Non-wt sessions should not appear as listed items
  ! echo "$output" | grep -q "^  work "
  [[ "$output" == *"Run with --apply"* ]]
}

@test "prune: dry-run does not kill sessions" {
  create_mock_tmux_for_prune
  mock_git_for_prune

  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 0 ]

  # Verify sessions still exist in state
  local state_file="$BATS_TEST_TMPDIR/tmux-state"
  grep -q "wt-feature-orphan" "$state_file"
  grep -q "wt-another-orphan" "$state_file"
}

@test "prune: --apply kills orphaned sessions and reports count" {
  create_mock_tmux_for_prune
  mock_git_for_prune

  run "$SHELL_CMD" "$TA_WORKSPACE" prune --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"killed orphaned session 'wt-feature-orphan'"* ]]
  [[ "$output" == *"killed orphaned session 'wt-another-orphan'"* ]]
  [[ "$output" == *"pruned 2 orphaned sessions"* ]]

  # Verify orphaned sessions are removed from state
  local state_file="$BATS_TEST_TMPDIR/tmux-state"
  ! grep -q "wt-feature-orphan" "$state_file"
  ! grep -q "wt-another-orphan" "$state_file"
  # Good session should still exist
  grep -q "wt-feature-good" "$state_file"
}

@test "prune: session in worktree subdirectory is not orphaned" {
  local state_file="$BATS_TEST_TMPDIR/tmux-state"
  local cwd_file="$BATS_TEST_TMPDIR/tmux-cwds"

  cat > "$state_file" <<'STATE'
wt-feature-subdir	1	0
STATE

  # CWD is a subdirectory of a valid worktree
  mkdir -p "$BATS_TEST_TMPDIR/worktrees/good/src/main"
  cat > "$cwd_file" <<STATE
wt-feature-subdir=$BATS_TEST_TMPDIR/worktrees/good/src/main
STATE

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
STATE_FILE="$state_file"
CWD_FILE="$cwd_file"
case "\$1" in
  list-sessions)
    if [[ "\$*" == *"#{session_name}"* ]]; then
      while IFS=\$'\t' read -r name windows attached; do
        echo "\$name"
      done < "\$STATE_FILE"
    fi
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
      name="\${target%:}"
      cwd="\$(grep "^\${name}=" "\$CWD_FILE" 2>/dev/null | cut -d= -f2)"
      echo "\${cwd:-/unknown}"
    fi
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"

  mock_git_for_prune

  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 0 ]
  [[ "$output" == *"no orphaned sessions"* ]]
}

@test "prune: session with existing dir but not a worktree is orphaned" {
  create_mock_tmux_for_prune
  mock_git_for_prune

  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 0 ]
  # not-a-worktree dir exists but is not in git worktree list
  [[ "$output" == *"wt-another-orphan"* ]]
}

@test "prune: non-wt sessions are ignored" {
  create_mock_tmux_for_prune
  mock_git_for_prune

  run "$SHELL_CMD" "$TA_WORKSPACE" prune
  [ "$status" -eq 0 ]
  [[ "$output" != *"main"* ]] || [[ "$output" == *"main"* && "$output" != *"  main "* ]]
  [[ "$output" != *"  work"* ]]
}

@test "create: --cmd flag applies to shell window only with agent windows" {
  create_mock_tmux_logging on
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --cmd "git status"
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  # Window 0 should be named "review", not "shell"
  grep -q "rename-window.*review" "$call_log"
  ! grep -q "rename-window.*shell" "$call_log"
  # Command sent to review window
  grep -q "send-keys.*:review git status" "$call_log"
}

@test "create: --layout agent creates shell and agent windows" {
  create_mock_tmux_logging
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --layout agent
  [ "$status" -eq 0 ]

  local call_log="$BATS_TEST_TMPDIR/tmux-call-log"
  # Should rename window 0 to shell
  grep -q "rename-window.*shell" "$call_log"
  # Should create agent window
  grep -q "new-window.*-n agent" "$call_log"
  # Should send claude to agent window
  grep -q "send-keys.*:agent.*claude" "$call_log"
  # Should focus agent window
  grep -q "select-window.*:agent" "$call_log"
}

@test "create: --layout and --cmd are mutually exclusive" {
  create_mock_tmux_logging
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --layout agent --cmd "echo hi"
  [ "$status" -eq 2 ]
  [[ "$output" == *"mutually exclusive"* ]]
}

@test "create: --layout unknown errors" {
  create_mock_tmux_logging
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --layout fancy
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown layout"* ]]
}

@test "create: existing session is idempotent regardless of flags" {
  create_mock_tmux_with_sessions
  create_mock_ta_wt

  run "$SHELL_CMD" "$TA_WORKSPACE" create feature/fix-thing --layout agent
  [ "$status" -eq 0 ]
  [[ "$output" == *"already exists"* ]]
}
