#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/ta-tmux (tmux introspection)
# Uses a mock tmux script since tmux server may not be available.

setup() {
  TA="${BATS_TEST_FILENAME%/*}/../scripts/ta"
  TA_TMUX="${BATS_TEST_FILENAME%/*}/../scripts/ta-tmux"

  # Create mock tmux directory
  MOCK_DIR="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_DIR"
  export TMUX_CMD="$MOCK_DIR/tmux"
}

# --- helper: create mock tmux ---
# Usage: create_mock_tmux <behavior>
# Behaviors: no-server, sessions, windows, panes, capture
create_mock_no_server() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
echo "no server running on /tmp/tmux-1000/default" >&2
exit 1
SCRIPT
  chmod +x "$TMUX_CMD"
}

create_mock_sessions() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions)
    printf "main\t3\t1\t1705312800\n"
    printf "wt-feature-fix\t2\t0\t1705316400\n"
    printf "work\t1\t0\t1705320000\n"
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

create_mock_windows() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions) exit 0 ;;
  list-windows)
    # Check if filtering by session
    if [[ "$*" == *"-t main:"* ]]; then
      printf "main\t0\tbash\t1\t1\n"
      printf "main\t1\tvim\t0\t1\n"
    else
      printf "main\t0\tbash\t1\t1\n"
      printf "main\t1\tvim\t0\t1\n"
      printf "wt-feature-fix\t0\tbash\t1\t2\n"
    fi
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

create_mock_panes() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions) exit 0 ;;
  list-panes)
    if [[ "$*" == *"-t main:"* ]]; then
      printf "%%0\tmain\t0\t0\tbash\t1234\t/home/user\n"
    else
      printf "%%0\tmain\t0\t0\tbash\t1234\t/home/user\n"
      printf "%%1\tmain\t1\t0\tvim\t1235\t/home/user/code\n"
      printf "%%2\twt-feature-fix\t0\t0\tbash\t1236\t/home/user/worktrees/fix\n"
    fi
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

create_mock_capture() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions) exit 0 ;;
  display-message)
    # Check if pane exists
    pane=""
    for arg in "$@"; do
      if [[ "$arg" == %* ]]; then
        pane="$arg"
      fi
    done
    if [[ -z "$pane" ]]; then
      # Parse -t argument
      for i in $(seq 1 $#); do
        if [[ "${!i}" == "-t" ]]; then
          next=$((i+1))
          pane="${!next}"
          break
        fi
      done
    fi
    if [[ "$pane" == "%99" ]]; then
      echo "can't find pane %99" >&2
      exit 1
    fi
    echo "$pane"
    ;;
  capture-pane)
    printf "$ git status\nOn branch main\nnothing to commit, working tree clean\n$ ls\nREADME.md  src/  tests/\n"
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# --- ta dispatcher tests ---

@test "ta tmux dispatches to ta-tmux" {
  create_mock_no_server
  run zsh "$TA" tmux
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage: ta tmux"* ]]
}

@test "ta-tmux with no args shows usage" {
  run zsh "$TA_TMUX"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "ta-tmux unknown command fails" {
  run zsh "$TA_TMUX" bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown tmux command"* ]]
}

# --- sessions tests ---

@test "tmux sessions with no server returns empty (text)" {
  create_mock_no_server
  run zsh "$TA_TMUX" sessions
  [ "$status" -eq 0 ]
}

@test "tmux sessions with no server returns empty JSON array" {
  create_mock_no_server
  run zsh "$TA_TMUX" sessions --json
  [ "$status" -eq 0 ]
  [ "$output" = "[]" ]
}

@test "tmux sessions text output shows sessions" {
  create_mock_sessions
  run zsh "$TA_TMUX" sessions
  [ "$status" -eq 0 ]
  [[ "$output" == *"SESSION"* ]]
  [[ "$output" == *"main"* ]]
  [[ "$output" == *"wt-feature-fix"* ]]
  [[ "$output" == *"work"* ]]
}

@test "tmux sessions --json returns valid JSON array" {
  create_mock_sessions
  run zsh "$TA_TMUX" sessions --json
  [ "$status" -eq 0 ]

  echo "$output" | jq . > /dev/null
  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 3 ]
}

@test "tmux sessions --json has correct fields" {
  create_mock_sessions
  run zsh "$TA_TMUX" sessions --json
  [ "$status" -eq 0 ]

  echo "$output" | jq -e '.[0] | has("name", "windows", "attached", "created")'
}

@test "tmux sessions --json has correct values" {
  create_mock_sessions
  run zsh "$TA_TMUX" sessions --json
  [ "$status" -eq 0 ]

  local name windows attached
  name="$(echo "$output" | jq -r '.[0].name')"
  windows="$(echo "$output" | jq '.[0].windows')"
  attached="$(echo "$output" | jq '.[0].attached')"

  [ "$name" = "main" ]
  [ "$windows" -eq 3 ]
  [ "$attached" -eq 1 ]
}

@test "tmux sessions unknown option fails" {
  run zsh "$TA_TMUX" sessions --bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option"* ]]
}

# --- windows tests ---

@test "tmux windows with no server returns empty (text)" {
  create_mock_no_server
  run zsh "$TA_TMUX" windows
  [ "$status" -eq 0 ]
}

@test "tmux windows with no server returns empty JSON array" {
  create_mock_no_server
  run zsh "$TA_TMUX" windows --json
  [ "$status" -eq 0 ]
  [ "$output" = "[]" ]
}

@test "tmux windows text output shows all windows" {
  create_mock_windows
  run zsh "$TA_TMUX" windows
  [ "$status" -eq 0 ]
  [[ "$output" == *"SESSION"* ]]
  [[ "$output" == *"main"* ]]
  [[ "$output" == *"vim"* ]]
  [[ "$output" == *"wt-feature-fix"* ]]
}

@test "tmux windows --json returns valid JSON" {
  create_mock_windows
  run zsh "$TA_TMUX" windows --json
  [ "$status" -eq 0 ]

  echo "$output" | jq . > /dev/null
  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 3 ]
}

@test "tmux windows --json has correct fields" {
  create_mock_windows
  run zsh "$TA_TMUX" windows --json
  [ "$status" -eq 0 ]

  echo "$output" | jq -e '.[0] | has("session", "index", "name", "active", "panes")'
}

@test "tmux windows --session filters to specific session" {
  create_mock_windows
  run zsh "$TA_TMUX" windows --session main --json
  [ "$status" -eq 0 ]

  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 2 ]

  # All results should be from 'main' session
  local sessions
  sessions="$(echo "$output" | jq -r '.[].session' | sort -u)"
  [ "$sessions" = "main" ]
}

# --- panes tests ---

@test "tmux panes with no server returns empty (text)" {
  create_mock_no_server
  run zsh "$TA_TMUX" panes
  [ "$status" -eq 0 ]
}

@test "tmux panes with no server returns empty JSON array" {
  create_mock_no_server
  run zsh "$TA_TMUX" panes --json
  [ "$status" -eq 0 ]
  [ "$output" = "[]" ]
}

@test "tmux panes --json returns valid JSON" {
  create_mock_panes
  run zsh "$TA_TMUX" panes --json
  [ "$status" -eq 0 ]

  echo "$output" | jq . > /dev/null
  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 3 ]
}

@test "tmux panes --json has correct fields" {
  create_mock_panes
  run zsh "$TA_TMUX" panes --json
  [ "$status" -eq 0 ]

  echo "$output" | jq -e '.[0] | has("pane_id", "session", "window", "pane", "command", "pid", "cwd")'
}

@test "tmux panes --json has correct values" {
  create_mock_panes
  run zsh "$TA_TMUX" panes --json
  [ "$status" -eq 0 ]

  local pane_id cmd pid cwd
  pane_id="$(echo "$output" | jq -r '.[0].pane_id')"
  cmd="$(echo "$output" | jq -r '.[0].command')"
  pid="$(echo "$output" | jq '.[0].pid')"
  cwd="$(echo "$output" | jq -r '.[0].cwd')"

  [ "$pane_id" = "%0" ]
  [ "$cmd" = "bash" ]
  [ "$pid" -eq 1234 ]
  [ "$cwd" = "/home/user" ]
}

@test "tmux panes --session filters to specific session" {
  create_mock_panes
  run zsh "$TA_TMUX" panes --session main --json
  [ "$status" -eq 0 ]

  local count
  count="$(echo "$output" | jq length)"
  [ "$count" -eq 1 ]
}

# --- capture tests ---

@test "tmux capture with no pane_id shows usage" {
  run zsh "$TA_TMUX" capture
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "tmux capture with no server returns error" {
  create_mock_no_server
  run zsh "$TA_TMUX" capture %0
  [ "$status" -eq 1 ]
  [[ "$output" == *"not running"* ]]
}

@test "tmux capture missing pane returns error" {
  create_mock_capture
  run zsh "$TA_TMUX" capture %99
  [ "$status" -eq 1 ]
  [[ "$output" == *"not found"* ]]
}

@test "tmux capture returns JSON with content" {
  create_mock_capture
  run zsh "$TA_TMUX" capture %0
  [ "$status" -eq 0 ]

  echo "$output" | jq . > /dev/null
  echo "$output" | jq -e 'has("pane_id", "lines", "content")'
}

@test "tmux capture has correct pane_id and default lines" {
  create_mock_capture
  run zsh "$TA_TMUX" capture %0
  [ "$status" -eq 0 ]

  local pane_id lines
  pane_id="$(echo "$output" | jq -r '.pane_id')"
  lines="$(echo "$output" | jq '.lines')"

  [ "$pane_id" = "%0" ]
  [ "$lines" -eq 120 ]
}

@test "tmux capture with --lines override" {
  create_mock_capture
  run zsh "$TA_TMUX" capture %0 --lines 50
  [ "$status" -eq 0 ]

  local lines
  lines="$(echo "$output" | jq '.lines')"
  [ "$lines" -eq 50 ]
}

@test "tmux capture content includes pane output" {
  create_mock_capture
  run zsh "$TA_TMUX" capture %0
  [ "$status" -eq 0 ]

  local content
  content="$(echo "$output" | jq -r '.content')"
  [[ "$content" == *"git status"* ]]
  [[ "$content" == *"README.md"* ]]
}
