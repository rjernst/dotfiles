#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for role setup via the zsh helper.
# Each test sets HOME to a temp dir and runs link_role through run_role.zsh.

HELPER="${BATS_TEST_FILENAME%/*}/helpers/run_role.zsh"

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export DOTFILES="${BATS_TEST_FILENAME%/*}/.."
  mkdir -p "$HOME/.zsh/plugins"
}

@test "git role succeeds (install script skips when config exists)" {
  mkdir -p "$HOME/.git" "$HOME/.ssh"
  touch "$HOME/.git/user.config" "$HOME/.ssh/allowed_signers"

  run zsh "$HELPER" git
  [ "$status" -eq 0 ]
  [[ "$output" == *"Adding role git"* ]]
}

@test "elasticsearch role creates gradle init symlink and zsh plugin" {
  mkdir -p "$HOME/.gradle/init.d"

  run zsh "$HELPER" elasticsearch
  [ "$status" -eq 0 ]

  [ -L "$HOME/.gradle/init.d/elasticsearch.gradle" ]
  [ -L "$HOME/.zsh/plugins/elasticsearch.zsh" ]
}

@test "elasticsearch-support role creates zsh plugin symlink" {
  run zsh "$HELPER" elasticsearch-support
  # May warn about gcloud missing — that's expected
  [ -L "$HOME/.zsh/plugins/elasticsearch-support.zsh" ]
}

@test "java role creates jenv dir and zsh plugin symlink" {
  # Stub jenv so role setup can call 'jenv enable-plugin'
  MOCK_BIN="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_BIN"
  cat > "$MOCK_BIN/jenv" <<'SCRIPT'
#!/bin/bash
exit 0
SCRIPT
  chmod +x "$MOCK_BIN/jenv"
  export PATH="$MOCK_BIN:$PATH"

  run zsh "$HELPER" java
  [ "$status" -eq 0 ]

  [ -d "$HOME/.jenv/versions" ]
  [ -L "$HOME/.zsh/plugins/java.zsh" ]
}

@test "jdk role creates zsh plugin symlink" {
  run zsh "$HELPER" jdk
  [ "$status" -eq 0 ]
  [ -L "$HOME/.zsh/plugins/jdk.zsh" ]
}

@test "node role creates zsh plugin symlink" {
  run zsh "$HELPER" node
  [ "$status" -eq 0 ]
  [ -L "$HOME/.zsh/plugins/node.zsh" ]
}
