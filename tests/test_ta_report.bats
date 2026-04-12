#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/ta-report (session report generator)
# Uses mock ta-wt, ta-workspace, and tmux scripts.

setup() {
  TA_REPORT="${BATS_TEST_FILENAME%/*}/../scripts/ta-report"

  # Create mock directory
  MOCK_DIR="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_DIR"
  export TMUX_CMD="$MOCK_DIR/tmux"
  export TA_WT_CMD="$MOCK_DIR/ta-wt"
  export TA_WORKSPACE_CMD="$MOCK_DIR/ta-workspace"

  # Create a real git repo for main branch info
  TEST_REPO="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$TEST_REPO"
  export GIT_CONFIG_GLOBAL="$BATS_TEST_TMPDIR/gitconfig"
  cat > "$GIT_CONFIG_GLOBAL" <<'EOF'
[user]
  name = Test User
  email = test@example.com
[init]
  defaultBranch = main
[commit]
  gpgsign = false
EOF
  git -C "$TEST_REPO" init -q
  git -C "$TEST_REPO" commit --allow-empty -m "initial" -q
}

# --- mock helpers ---

# Mock ta-wt status --json with worktrees
create_mock_ta_wt_with_worktrees() {
  cat > "$TA_WT_CMD" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "status" && "$2" == "--json" ]]; then
  cat <<'JSON'
[
  {"branch": "feature/fix-thing", "status": "ready", "ahead": 3, "behind": 0, "dirty": "clean", "path": "/home/user/code/es-fix"},
  {"branch": "bugfix/oom", "status": "wip", "ahead": 1, "behind": 0, "dirty": "dirty(2M)", "path": "/home/user/code/es-oom"},
  {"branch": "stale/old", "status": "merged", "ahead": 0, "behind": 0, "dirty": "clean", "path": "/home/user/code/es-old"}
]
JSON
else
  echo "ta-wt mock: unsupported args: $*" >&2
  exit 1
fi
SCRIPT
  chmod +x "$TA_WT_CMD"
}

# Mock ta-wt status --json with no worktrees
create_mock_ta_wt_empty() {
  cat > "$TA_WT_CMD" <<'SCRIPT'
#!/usr/bin/env bash
if [[ "$1" == "status" && "$2" == "--json" ]]; then
  echo "[]"
else
  echo "ta-wt mock: unsupported args: $*" >&2
  exit 1
fi
SCRIPT
  chmod +x "$TA_WT_CMD"
}

# Mock tmux with wt-* sessions
create_mock_tmux_with_sessions() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions)
    printf "main\t3\t1\n"
    printf "wt-feature-fix-thing\t2\t1\n"
    printf "wt-bugfix-oom\t1\t0\n"
    ;;
  display-message)
    target=""
    fmt=""
    shift
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -t) target="$2"; shift 2 ;;
        -p) fmt="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    if [[ "$fmt" == *"pane_current_path"* ]]; then
      name="${target%:}"
      case "$name" in
        wt-feature-fix-thing) echo "/home/user/code/es-fix" ;;
        wt-bugfix-oom) echo "/home/user/code/es-oom" ;;
        *) echo "/tmp/unknown" ;;
      esac
    fi
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
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

# Mock tmux with no wt-* sessions
create_mock_tmux_no_wt_sessions() {
  cat > "$TMUX_CMD" <<'SCRIPT'
#!/usr/bin/env bash
case "$1" in
  list-sessions)
    printf "main\t3\t1\n"
    printf "work\t1\t0\n"
    ;;
  *) exit 0 ;;
esac
SCRIPT
  chmod +x "$TMUX_CMD"
}

# --- dispatcher test ---

@test "ta report dispatches to ta-report" {
  command -v ta >/dev/null 2>&1 || skip "ta binary not installed"
  create_mock_ta_wt_empty
  create_mock_tmux_no_server
  run ta report --repo "$TEST_REPO"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Workspace Report"* ]]
}

# --- report with mixed worktree states ---

@test "report: renders worktree table with mixed states" {
  create_mock_ta_wt_with_worktrees
  create_mock_tmux_with_sessions

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  # Check markdown header
  [[ "$output" == *"# Workspace Report"* ]]
  [[ "$output" == *"## Worktrees"* ]]

  # Check table header
  [[ "$output" == *"| Branch | Status | Ahead | Behind | Workspace | Path |"* ]]
  [[ "$output" == *"|--------|--------|-------|--------|-----------|------|"* ]]

  # Check data rows
  [[ "$output" == *"feature/fix-thing"* ]]
  [[ "$output" == *"ready"* ]]
  [[ "$output" == *"bugfix/oom"* ]]
  [[ "$output" == *"wip"* ]]
  [[ "$output" == *"stale/old"* ]]
  [[ "$output" == *"merged"* ]]
}

@test "report: correlates worktrees with workspace sessions" {
  create_mock_ta_wt_with_worktrees
  create_mock_tmux_with_sessions

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  # feature/fix-thing should be "attached" (session exists, attached=1)
  [[ "$output" == *"feature/fix-thing | ready | 3 | 0 | attached"* ]]

  # bugfix/oom should be "detached" (session exists, attached=0)
  [[ "$output" == *"bugfix/oom | wip | 1 | 0 | detached"* ]]

  # stale/old should be "—" (no session)
  [[ "$output" == *"stale/old | merged | 0 | 0 | —"* ]]
}

# --- report with no workspaces ---

@test "report: omits sessions section content when no tmux server" {
  create_mock_ta_wt_with_worktrees
  create_mock_tmux_no_server

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  [[ "$output" == *"## Active Sessions"* ]]
  [[ "$output" == *"No workspace sessions."* ]]
}

@test "report: omits sessions content when no wt-* sessions" {
  create_mock_ta_wt_with_worktrees
  create_mock_tmux_no_wt_sessions

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  [[ "$output" == *"## Active Sessions"* ]]
  [[ "$output" == *"No workspace sessions."* ]]
}

# --- report with no worktrees ---

@test "report: with no worktrees renders empty message" {
  create_mock_ta_wt_empty
  create_mock_tmux_no_server

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  [[ "$output" == *"## Worktrees"* ]]
  [[ "$output" == *"No active worktrees"* ]]
}

# --- active sessions table ---

@test "report: active sessions table shows wt-* sessions" {
  create_mock_ta_wt_with_worktrees
  create_mock_tmux_with_sessions

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  [[ "$output" == *"## Active Sessions"* ]]
  [[ "$output" == *"| Session | Windows | CWD |"* ]]
  [[ "$output" == *"wt-feature-fix-thing"* ]]
  [[ "$output" == *"wt-bugfix-oom"* ]]
}

# --- main branch info ---

@test "report: shows main branch section" {
  create_mock_ta_wt_empty
  create_mock_tmux_no_server

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  [[ "$output" == *"## Main Branch"* ]]
  [[ "$output" == *"Last fetched:"* ]]
  [[ "$output" == *"Behind upstream:"* ]]
}

@test "report: stale main shows behind count" {
  create_mock_ta_wt_empty
  create_mock_tmux_no_server

  # Create a remote with extra commits
  REMOTE_REPO="$BATS_TEST_TMPDIR/remote"
  git clone -q "$TEST_REPO" "$REMOTE_REPO"
  git -C "$TEST_REPO" remote add upstream "$REMOTE_REPO"
  # Add commits to remote
  git -C "$REMOTE_REPO" commit --allow-empty -m "remote commit 1" -q
  git -C "$REMOTE_REPO" commit --allow-empty -m "remote commit 2" -q
  git -C "$TEST_REPO" fetch -q upstream

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]
  [[ "$output" == *"Behind upstream: 2 commits"* ]]
}

# --- markdown well-formedness ---

@test "report: markdown output is well-formed (header, separator, data rows)" {
  create_mock_ta_wt_with_worktrees
  create_mock_tmux_with_sessions

  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]

  # Count table structure: header + separator + data rows for worktrees
  local header_count separator_count data_count
  header_count=$(echo "$output" | grep -c "| Branch | Status |" || true)
  separator_count=$(echo "$output" | grep -c "|--------|--------|" || true)
  data_count=$(echo "$output" | grep -c "^| .* | .* | [0-9]" || true)

  [ "$header_count" -eq 1 ]
  [ "$separator_count" -eq 1 ]
  [ "$data_count" -eq 3 ]
}

# --- --repo flag ---

@test "report: --repo flag changes git context" {
  create_mock_ta_wt_empty
  create_mock_tmux_no_server

  # Run from a different directory but point to the test repo
  run zsh "$TA_REPORT" --repo "$TEST_REPO"
  [ "$status" -eq 0 ]
  [[ "$output" == *"# Workspace Report"* ]]
}

@test "report: --repo with nonexistent path fails" {
  run zsh "$TA_REPORT" --repo /nonexistent/path
  [ "$status" -eq 1 ]
  [[ "$output" == *"repo path not found"* ]]
}

# --- unknown option ---

@test "report: unknown option fails" {
  run zsh "$TA_REPORT" --bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option"* ]]
}
