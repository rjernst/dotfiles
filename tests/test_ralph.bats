#!/usr/bin/env bats

# CLI integration tests for scripts/ralph (Python rewrite).
# Tests argument parsing, help/usage, and validation logic.
# End-to-end orchestration (sandbox, proxy, token) is tested in
# tests/test_ralph.py via mocking.

setup() {
  RALPH="${BATS_TEST_FILENAME%/*}/../scripts/ralph"
  export TMPDIR="$BATS_TEST_TMPDIR"

  # Create a project dir
  PROJECT="$BATS_TEST_TMPDIR/project"
  mkdir -p "$PROJECT"

  # Stub bin directory
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"

  # Stub docker
  cat > "$BATS_TEST_TMPDIR/bin/docker" <<'STUB'
#!/bin/bash
exit 0
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/docker"

  # Stub gh
  cat > "$BATS_TEST_TMPDIR/bin/gh" <<'STUB'
#!/bin/bash
echo '{}'
STUB
  chmod +x "$BATS_TEST_TMPDIR/bin/gh"
}

# --- help / usage tests ---

@test "ralph --help shows usage" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"--issue"* ]]
  [[ "$output" == *"--poll"* ]]
}

@test "ralph -h shows usage" {
  run "$RALPH" -h
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
}

@test "ralph fails with unknown option" {
  cd "$PROJECT"
  run "$RALPH" --bogus
  [ "$status" -eq 1 ]
  [[ "$output" == *"unknown option"* ]]
}

@test "ralph with no args shows usage" {
  cd "$PROJECT"
  run "$RALPH"
  [ "$status" -eq 2 ]
  [[ "$output" == *"no mode specified"* ]]
  [[ "$output" == *"--issue"* ]]
  [[ "$output" == *"--poll"* ]]
}

# --- prerequisite tests ---

@test "ralph fails when gh is not installed" {
  cd "$PROJECT"
  # Remove gh stub from PATH
  rm "$BATS_TEST_TMPDIR/bin/gh"
  # Ensure python3 is available
  ln -sf "$(command -v python3)" "$BATS_TEST_TMPDIR/bin/python3"
  run env PATH="$BATS_TEST_TMPDIR/bin:/usr/bin" "$RALPH" --issue 1
  [ "$status" -eq 1 ]
  [[ "$output" == *"gh is not installed"* ]]
}

# --- validation tests ---

@test "ralph --poll and --issue together errors" {
  cd "$PROJECT"
  run "$RALPH" --poll --issue 42
  [ "$status" -eq 2 ]
  [[ "$output" == *"--poll and --issue cannot be used together"* ]]
}

@test "ralph --interval without --poll errors" {
  cd "$PROJECT"
  run "$RALPH" --interval 10s
  [ "$status" -eq 2 ]
  [[ "$output" == *"--interval requires --poll"* ]]
}

@test "ralph --timeout without --poll errors" {
  cd "$PROJECT"
  run "$RALPH" --timeout 1s
  [ "$status" -eq 2 ]
  [[ "$output" == *"--timeout requires --poll"* ]]
}

@test "ralph --help shows --poll option" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--poll"* ]]
  [[ "$output" == *"--interval"* ]]
}

@test "ralph --help shows --issue option" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--issue"* ]]
}

@test "ralph --help shows --rebuild option" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--rebuild"* ]]
}

@test "ralph --help shows --agent option" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--agent"* ]]
}

@test "ralph --help shows token commands" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"store-token"* ]]
  [[ "$output" == *"check-token"* ]]
  [[ "$output" == *"get-token"* ]]
}

@test "ralph --help shows sandbox commands" {
  run "$RALPH" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"prune-sandboxes"* ]]
}
