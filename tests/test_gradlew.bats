#!/usr/bin/env bats

# Tests for scripts/gradlew.sh
# Uses temp directories to simulate project structures with a fake gradlew.

setup() {
  GRADLEW_SCRIPT="${BATS_TEST_FILENAME%/*}/../scripts/gradlew.sh"

  export PROJECT="$BATS_TEST_TMPDIR/project"
  mkdir -p "$PROJECT"

  # Create a fake gradlew that just prints its directory and exits
  cat > "$PROJECT/gradlew" <<'SCRIPT'
#!/bin/bash
echo "gradlew:$(dirname "$0")"
SCRIPT
  chmod +x "$PROJECT/gradlew"
}

@test "finds gradlew in current directory" {
  cd "$PROJECT"
  run bash "$GRADLEW_SCRIPT"

  [ "$status" -eq 0 ]
  [[ "$output" == *"gradlew:$PROJECT"* ]]
}

@test "finds gradlew in parent directory" {
  mkdir -p "$PROJECT/subdir"
  cd "$PROJECT/subdir"
  run bash "$GRADLEW_SCRIPT"

  [ "$status" -eq 0 ]
  [[ "$output" == *"gradlew:$PROJECT"* ]]
}

@test "finds gradlew in grandparent directory" {
  mkdir -p "$PROJECT/a/b"
  cd "$PROJECT/a/b"
  run bash "$GRADLEW_SCRIPT"

  [ "$status" -eq 0 ]
  [[ "$output" == *"gradlew:$PROJECT"* ]]
}

@test "fails at root when no gradlew exists" {
  local empty="$BATS_TEST_TMPDIR/empty"
  mkdir -p "$empty"
  cd "$empty"
  run bash "$GRADLEW_SCRIPT"

  [ "$status" -eq 1 ]
  [[ "$output" == *"Could not find gradle wrapper"* ]]
}
