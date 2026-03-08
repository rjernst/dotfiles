#!/usr/bin/env bats

# Tests for docker/ralph/entrypoint.sh loop logic.
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

  # Stub claude — just echo and succeed
  CLAUDE_LOG="$BATS_TEST_TMPDIR/claude.log"
  export CLAUDE_LOG
  cat > "$BATS_TEST_TMPDIR/bin/claude" <<'STUB'
#!/bin/bash
echo "claude invoked: $*" >> "$CLAUDE_LOG"
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/claude"

  # Stub git — record calls, succeed
  GIT_LOG="$BATS_TEST_TMPDIR/git.log"
  export GIT_LOG
  cat > "$BATS_TEST_TMPDIR/bin/git" <<'STUB'
#!/bin/bash
echo "git $*" >> "$GIT_LOG"
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/git"

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

@test "entrypoint stops at max iterations" {
  export MAX_ITERATIONS=3
  run_entrypoint
  [ "$status" -eq 0 ]
  [[ "$output" == *"reached max iterations (3)"* ]]
}

@test "entrypoint runs correct number of iterations" {
  export MAX_ITERATIONS=3
  run_entrypoint
  [ "$status" -eq 0 ]
  # Claude should be invoked 3 times
  [ "$(grep -c 'claude invoked' "$CLAUDE_LOG")" -eq 3 ]
}

@test "entrypoint passes model to claude" {
  export MAX_ITERATIONS=1
  export MODEL=opus
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q -- '--model opus' "$CLAUDE_LOG"
}

@test "entrypoint uses --dangerously-skip-permissions" {
  export MAX_ITERATIONS=1
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q -- '--dangerously-skip-permissions' "$CLAUDE_LOG"
}

@test "entrypoint prints iteration banners" {
  export MAX_ITERATIONS=2
  run_entrypoint
  [ "$status" -eq 0 ]
  [[ "$output" == *"iteration 1"* ]]
  [[ "$output" == *"iteration 2"* ]]
}

@test "entrypoint configures git identity" {
  export MAX_ITERATIONS=1
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q 'git config --global user.name testuser' "$GIT_LOG"
  grep -q 'git config --global user.email test@test.com' "$GIT_LOG"
}

@test "entrypoint marks /work as safe directory" {
  export MAX_ITERATIONS=1
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q 'git config --global --add safe.directory /work' "$GIT_LOG"
}

@test "entrypoint pushes when PUSH=1" {
  export MAX_ITERATIONS=1
  export PUSH=1
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q 'git push' "$GIT_LOG"
}

@test "entrypoint does not push when PUSH=0" {
  export MAX_ITERATIONS=1
  export PUSH=0
  run_entrypoint
  [ "$status" -eq 0 ]
  ! grep -q 'git push' "$GIT_LOG"
}

@test "entrypoint continues when claude fails" {
  # Replace claude stub with one that fails
  cat > "$BATS_TEST_TMPDIR/bin/claude" <<'STUB'
#!/bin/bash
exit 1
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/claude"

  export MAX_ITERATIONS=2
  run_entrypoint
  [ "$status" -eq 0 ]
  [[ "$output" == *"iteration 1"* ]]
  [[ "$output" == *"iteration 2"* ]]
  [[ "$output" == *"reached max iterations"* ]]
}

@test "entrypoint creates writable gitconfig copy" {
  # Simulate a read-only host gitconfig
  echo "[user]\n\tname = host" > "$HOME/.gitconfig"
  chmod 444 "$HOME/.gitconfig"

  export MAX_ITERATIONS=1
  run_entrypoint
  [ "$status" -eq 0 ]
  # The writable copy should exist
  [ -f "$HOME/.ralph-gitconfig" ]
}

@test "entrypoint defaults model to sonnet" {
  export MAX_ITERATIONS=1
  unset MODEL
  run_entrypoint
  [ "$status" -eq 0 ]
  grep -q -- '--model sonnet' "$CLAUDE_LOG"
}
