#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/ta-agent-loop (Ralph polling loop manager)
# Uses mock tmux and git since tmux server may not be available.

setup() {
  TA_AGENT_LOOP="${BATS_TEST_FILENAME%/*}/../scripts/ta-agent-loop"

  # Create mock directory
  MOCK_DIR="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_DIR"
  export TMUX_CMD="$MOCK_DIR/tmux"
  export RALPH_CMD="ralph"

  # Create a fake git repo for testing
  REPO_DIR="$BATS_TEST_TMPDIR/fakerepo"
  mkdir -p "$REPO_DIR/.git"
  git -C "$REPO_DIR" init -q
  git -C "$REPO_DIR" remote add origin "https://github.com/rjernst/elasticsearch.git"
}

# Detect available shell (zsh preferred, bash fallback)
if command -v zsh &>/dev/null; then
  SHELL_CMD=zsh
else
  SHELL_CMD=bash
fi

# --- mock helpers ---

# Mock tmux: no server running
create_mock_tmux_no_server() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
echo "no server running on /tmp/tmux-1000/default" >&2
exit 1
SCRIPT
  chmod +x "$TMUX_CMD"
}

# Mock tmux: tracks state via files
# Supports: new-session, has-session, list-sessions, new-window, list-windows,
#           send-keys, kill-window, kill-session
create_mock_tmux_stateful() {
  local state_dir="$BATS_TEST_TMPDIR/tmux-state"
  mkdir -p "$state_dir"

  cat > "$TMUX_CMD" <<SCRIPT
#!/usr/bin/env bash
STATE_DIR="$state_dir"

cmd_list_sessions() {
  if [[ -d "\$STATE_DIR/sessions" ]] && ls "\$STATE_DIR/sessions"/* &>/dev/null; then
    for f in "\$STATE_DIR/sessions"/*; do
      basename "\$f"
    done
    exit 0
  fi
  echo "no server running" >&2
  exit 1
}

cmd_has_session() {
  shift
  [[ "\$1" == "-t" ]] && shift
  local session="\$1"
  [[ -f "\$STATE_DIR/sessions/\$session" ]]
}

cmd_new_session() {
  shift
  local session="" dir="" window_name=""
  while [[ \$# -gt 0 ]]; do
    case "\$1" in
      -d) shift ;;
      -s) session="\$2"; shift 2 ;;
      -c) dir="\$2"; shift 2 ;;
      -n) window_name="\$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  mkdir -p "\$STATE_DIR/sessions"
  touch "\$STATE_DIR/sessions/\$session"
  if [[ -n "\$window_name" ]]; then
    mkdir -p "\$STATE_DIR/windows/\$session"
    echo "\$dir" > "\$STATE_DIR/windows/\$session/\$window_name"
  fi
}

cmd_new_window() {
  shift
  local session="" window_name="" dir=""
  while [[ \$# -gt 0 ]]; do
    case "\$1" in
      -t) session="\$2"; shift 2 ;;
      -n) window_name="\$2"; shift 2 ;;
      -c) dir="\$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  mkdir -p "\$STATE_DIR/windows/\$session"
  echo "\$dir" > "\$STATE_DIR/windows/\$session/\$window_name"
}

cmd_list_windows() {
  shift
  local session="" fmt=""
  while [[ \$# -gt 0 ]]; do
    case "\$1" in
      -t) session="\$2"; shift 2 ;;
      -F) fmt="\$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  if [[ -d "\$STATE_DIR/windows/\$session" ]] && ls "\$STATE_DIR/windows/\$session"/* &>/dev/null; then
    for f in "\$STATE_DIR/windows/\$session"/*; do
      local name="\$(basename "\$f")"
      local cwd="\$(cat "\$f")"
      # Simulate tmux format strings
      if [[ "\$fmt" == *"pane_current_path"* ]]; then
        echo -e "\${name}\t\${cwd}"
      else
        echo "\${name}"
      fi
    done
    exit 0
  fi
  exit 1
}

cmd_send_keys() {
  shift
  local target="" keys=""
  while [[ \$# -gt 0 ]]; do
    case "\$1" in
      -t) target="\$2"; shift 2 ;;
      Enter) shift ;;
      *) keys="\$1"; shift ;;
    esac
  done
  echo "\$target: \$keys" >> "\$STATE_DIR/sent-keys"
}

cmd_kill_window() {
  shift
  local target=""
  while [[ \$# -gt 0 ]]; do
    case "\$1" in
      -t) target="\$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  local session="\${target%%:*}"
  local window="\${target#*:}"
  rm -f "\$STATE_DIR/windows/\$session/\$window"
  if [[ -d "\$STATE_DIR/windows/\$session" ]] && ! ls "\$STATE_DIR/windows/\$session"/* &>/dev/null; then
    rm -f "\$STATE_DIR/sessions/\$session"
    rm -rf "\$STATE_DIR/windows/\$session"
  fi
}

cmd_kill_session() {
  shift
  local session=""
  while [[ \$# -gt 0 ]]; do
    case "\$1" in
      -t) session="\$2"; shift 2 ;;
      *) shift ;;
    esac
  done
  rm -f "\$STATE_DIR/sessions/\$session"
  rm -rf "\$STATE_DIR/windows/\$session"
}

case "\$1" in
  list-sessions) cmd_list_sessions "\$@" ;;
  has-session) cmd_has_session "\$@" ;;
  new-session) cmd_new_session "\$@" ;;
  new-window) cmd_new_window "\$@" ;;
  list-windows) cmd_list_windows "\$@" ;;
  send-keys) cmd_send_keys "\$@" ;;
  kill-window) cmd_kill_window "\$@" ;;
  kill-session) cmd_kill_session "\$@" ;;
  *)
    echo "mock tmux: unknown command \$1" >&2
    exit 1
    ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
  echo "$state_dir"
}

# --- usage tests ---

@test "no args shows usage" {
  run $SHELL_CMD "$TA_AGENT_LOOP" 2>&1
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: ta agent-loop"* ]]
}

@test "unknown subcommand fails" {
  run $SHELL_CMD "$TA_AGENT_LOOP" foo 2>&1
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown agent-loop command"* ]]
}

# --- start tests ---

@test "start creates session and window" {
  local state_dir
  state_dir="$(create_mock_tmux_stateful)"

  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"started agent loop for 'elasticsearch'"* ]]

  # Session should exist
  [ -f "$state_dir/sessions/agent-loops" ]
  # Window should exist
  [ -f "$state_dir/windows/agent-loops/elasticsearch" ]
  # Ralph command should have been sent
  [[ "$(cat "$state_dir/sent-keys")" == *"ralph --poll"* ]]
}

@test "start is idempotent" {
  local state_dir
  state_dir="$(create_mock_tmux_stateful)"

  # First start
  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"started agent loop"* ]]

  # Verify state was created
  [ -f "$state_dir/sessions/agent-loops" ]

  # Second start — idempotent
  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"already running"* ]]
}

@test "start passes ralph flags through" {
  local state_dir
  state_dir="$(create_mock_tmux_stateful)"

  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR" -- --model opus --packages "jq curl"
  [ "$status" -eq 0 ]
  [[ "$(cat "$state_dir/sent-keys")" == *"ralph --poll --model opus --packages jq curl"* ]]
}

@test "start fails outside git repo" {
  create_mock_tmux_stateful
  local non_git="$BATS_TEST_TMPDIR/not-a-repo"
  mkdir -p "$non_git"

  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$non_git"
  [ "$status" -eq 1 ]
  [[ "$output" == *"not a git repository"* ]]
}

@test "start fails with no origin remote" {
  create_mock_tmux_stateful
  local no_remote="$BATS_TEST_TMPDIR/no-remote"
  mkdir -p "$no_remote"
  git -C "$no_remote" init -q

  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$no_remote"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no 'origin' remote"* ]]
}

@test "start with --dir flag uses specified directory" {
  local state_dir
  state_dir="$(create_mock_tmux_stateful)"

  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"
  [ "$status" -eq 0 ]
  # Window directory should be the repo dir
  local stored_dir
  stored_dir="$(cat "$state_dir/windows/agent-loops/elasticsearch")"
  [ "$stored_dir" = "$REPO_DIR" ]
}

# --- stop tests ---

@test "stop kills the window" {
  local state_dir
  state_dir="$(create_mock_tmux_stateful)"

  # Start first
  $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"

  run $SHELL_CMD "$TA_AGENT_LOOP" stop elasticsearch
  [ "$status" -eq 0 ]
  [[ "$output" == *"stopped agent loop for 'elasticsearch'"* ]]
}

@test "stop on last window also removes session" {
  local state_dir
  state_dir="$(create_mock_tmux_stateful)"

  # Start a single loop
  $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"

  # Stop it
  run $SHELL_CMD "$TA_AGENT_LOOP" stop elasticsearch
  [ "$status" -eq 0 ]

  # Session should be gone
  [ ! -f "$state_dir/sessions/agent-loops" ]
}

@test "stop fails for nonexistent repo" {
  create_mock_tmux_stateful

  run $SHELL_CMD "$TA_AGENT_LOOP" stop nonexistent
  [ "$status" -eq 1 ]
  [[ "$output" == *"no agent loop"* ]]
}

@test "stop with no args shows usage" {
  run $SHELL_CMD "$TA_AGENT_LOOP" stop 2>&1
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

# --- list tests ---

@test "list with no loops shows message" {
  create_mock_tmux_no_server

  run $SHELL_CMD "$TA_AGENT_LOOP" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"no agent loops running"* ]]
}

@test "list shows running loops" {
  create_mock_tmux_stateful

  # Start a loop
  $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"

  run $SHELL_CMD "$TA_AGENT_LOOP" list
  [ "$status" -eq 0 ]
  [[ "$output" == *"REPO"* ]]
  [[ "$output" == *"elasticsearch"* ]]
}

# --- repo name extraction ---

@test "repo name from HTTPS URL" {
  create_mock_tmux_stateful
  # Already set up with HTTPS URL in setup()
  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$REPO_DIR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"elasticsearch"* ]]
}

@test "repo name from SSH URL" {
  create_mock_tmux_stateful
  local ssh_repo="$BATS_TEST_TMPDIR/ssh-repo"
  mkdir -p "$ssh_repo"
  git -C "$ssh_repo" init -q
  git -C "$ssh_repo" remote add origin "git@github.com:elastic/elasticsearch.git"

  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$ssh_repo"
  [ "$status" -eq 0 ]
  [[ "$output" == *"elasticsearch"* ]]
}

@test "repo name from URL without .git suffix" {
  create_mock_tmux_stateful
  local repo="$BATS_TEST_TMPDIR/no-git-suffix"
  mkdir -p "$repo"
  git -C "$repo" init -q
  git -C "$repo" remote add origin "https://github.com/rjernst/dotfiles"

  run $SHELL_CMD "$TA_AGENT_LOOP" start --dir "$repo"
  [ "$status" -eq 0 ]
  [[ "$output" == *"dotfiles"* ]]
}
