#!/usr/bin/env bats

# Tests for docker/ralph/entrypoint.sh single iteration logic.
# Stubs claude and git so no real agent or container is needed.

setup() {
  ENTRYPOINT="${BATS_TEST_FILENAME%/*}/../docker/ralph/entrypoint.sh"

  # Create fake home and work directories
  export HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$HOME"
  WORKDIR="$BATS_TEST_TMPDIR/work"
  mkdir -p "$WORKDIR"
  echo "test prompt" > "$WORKDIR/PROMPT.md"

  # Stub bin directory
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  # Stub git — record calls, simulate HEAD advancing on each rev-parse
  GIT_LOG="$BATS_TEST_TMPDIR/git.log"
  export GIT_LOG
  GIT_HEAD_COUNTER="$BATS_TEST_TMPDIR/head_counter"
  export GIT_HEAD_COUNTER
  echo "0" > "$GIT_HEAD_COUNTER"
  cat > "$BATS_TEST_TMPDIR/bin/git" <<'STUB'
#!/bin/bash
echo "git $*" >> "$GIT_LOG"
if [[ "$1" == "rev-parse" && "$2" == "HEAD" ]]; then
  count=$(cat "$GIT_HEAD_COUNTER")
  echo "fakehash$count"
fi
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/git"

  # Stub claude — log invocation and simulate a commit by advancing HEAD
  CLAUDE_LOG="$BATS_TEST_TMPDIR/claude.log"
  export CLAUDE_LOG
  cat > "$BATS_TEST_TMPDIR/bin/claude" <<'STUB'
#!/bin/bash
echo "claude invoked: $*" >> "$CLAUDE_LOG"
count=$(cat "$GIT_HEAD_COUNTER")
echo "$((count + 1))" > "$GIT_HEAD_COUNTER"
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/claude"

  # Default environment
  export PROMPT_FILE=PROMPT.md
  export MODEL=sonnet
  export PUSH=0
  export GIT_USER=testuser
  export GIT_EMAIL=test@test.com
}

run_entrypoint() {
  cd "$WORKDIR"
  run bash "$ENTRYPOINT"
}

@test "entrypoint runs claude exactly once" {
  run_entrypoint
  [ "$status" -eq 0 ]
  [ "$(grep -c 'claude invoked' "$CLAUDE_LOG")" -eq 1 ]
}

@test "entrypoint passes model to claude" {
  export MODEL=opus
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q -- '--model opus' "$CLAUDE_LOG"
}

@test "entrypoint uses --dangerously-skip-permissions" {
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q -- '--dangerously-skip-permissions' "$CLAUDE_LOG"
}

@test "entrypoint configures git identity" {
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q 'git config --global user.name testuser' "$GIT_LOG"
  grep -q 'git config --global user.email test@test.com' "$GIT_LOG"
}

@test "entrypoint marks /work as safe directory" {
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q 'git config --global --add safe.directory /work' "$GIT_LOG"
}

@test "entrypoint pushes when PUSH=1" {
  export PUSH=1
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q 'git push' "$GIT_LOG"
}

@test "entrypoint does not push when PUSH=0" {
  export PUSH=0
  run_entrypoint
  [ "$status" -eq 0 ]
  ! grep -q 'git push' "$GIT_LOG"
}

@test "entrypoint prints no-commit message when HEAD unchanged" {
  # Claude succeeds but doesn't advance HEAD
  cat > "$BATS_TEST_TMPDIR/bin/claude" <<'STUB'
#!/bin/bash
echo "claude invoked: $*" >> "$CLAUDE_LOG"
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/claude"

  run_entrypoint
  [ "$status" -eq 0 ]
  [[ "$output" == *"no commit made"* ]]
}

@test "entrypoint does not print no-commit message when HEAD changes" {
  run_entrypoint
  [ "$status" -eq 0 ]
  [[ "$output" != *"no commit made"* ]]
}

@test "entrypoint handles claude error gracefully" {
  cat > "$BATS_TEST_TMPDIR/bin/claude" <<'STUB'
#!/bin/bash
exit 1
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/claude"

  run_entrypoint
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude exited with error"* ]]
  [[ "$output" == *"no commit made"* ]]
}

@test "entrypoint creates writable gitconfig copy" {
  echo "[user]\n\tname = host" > "$HOME/.gitconfig"
  chmod 444 "$HOME/.gitconfig"

  run_entrypoint
  [ "$status" -eq 0 ]
  [ -f "$HOME/.ralph-gitconfig" ]
}

@test "entrypoint defaults model to sonnet" {
  unset MODEL
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q -- '--model sonnet' "$CLAUDE_LOG"
}
