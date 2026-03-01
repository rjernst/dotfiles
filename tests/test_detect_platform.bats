#!/usr/bin/env bats

# Tests for scripts/detect-platform

setup() {
  DETECT_SCRIPT="${BATS_TEST_FILENAME%/*}/../scripts/detect-platform"
}

@test "outputs one of the known platforms" {
  run bash "$DETECT_SCRIPT"

  [ "$status" -eq 0 ]
  [[ "$output" == "macos" || "$output" == "arch" || "$output" == "unknown" ]]
}

@test "on macOS, outputs macos" {
  if [ "$(uname)" != "Darwin" ]; then
    skip "not running on macOS"
  fi

  run bash "$DETECT_SCRIPT"

  [ "$status" -eq 0 ]
  [ "$output" = "macos" ]
}
