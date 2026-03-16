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
  # Stub required commands (tsh, kubectl, jq)
  MOCK_BIN="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_BIN"
  for cmd in tsh kubectl jq; do
    printf '#!/bin/bash\nexit 0\n' > "$MOCK_BIN/$cmd"
    chmod +x "$MOCK_BIN/$cmd"
  done
  export PATH="$MOCK_BIN:$PATH"

  run zsh "$HELPER" elasticsearch-support
  [ -L "$HOME/.zsh/plugins/elasticsearch-support.zsh" ]
}

@test "java role creates jenv dir and zsh plugin symlink" {
  # Stub jenv so role setup can call 'jenv init -' and 'jenv enable-plugin'
  MOCK_BIN="$BATS_TEST_TMPDIR/mock-bin"
  MOCK_LOG="$BATS_TEST_TMPDIR/jenv-calls.log"
  mkdir -p "$MOCK_BIN"
  cat > "$MOCK_BIN/jenv" <<SCRIPT
#!/bin/bash
echo "\$*" >> "$MOCK_LOG"
if [ "\$1" = "init" ]; then
  # jenv init outputs shell setup; emit a no-op so eval succeeds
  echo "true"
fi
exit 0
SCRIPT
  chmod +x "$MOCK_BIN/jenv"
  export PATH="$MOCK_BIN:$PATH"

  run zsh "$HELPER" java
  [ "$status" -eq 0 ]

  [ -d "$HOME/.jenv/versions" ]
  [ -L "$HOME/.zsh/plugins/java.zsh" ]

  # Verify jenv was initialized and plugins were enabled
  grep -q "^init -$" "$MOCK_LOG"
  grep -q "^enable-plugin gradle$" "$MOCK_LOG"
  grep -q "^enable-plugin export$" "$MOCK_LOG"
}

@test "jdk role creates zsh plugin symlink" {
  run zsh "$HELPER" jdk
  [ "$status" -eq 0 ]
  [ -L "$HOME/.zsh/plugins/jdk.zsh" ]
}

@test "node role creates zsh plugin symlink" {
  # Stub fnm so requires_cmds check passes
  MOCK_BIN="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_BIN"
  printf '#!/bin/bash\nexit 0\n' > "$MOCK_BIN/fnm"
  chmod +x "$MOCK_BIN/fnm"
  export PATH="$MOCK_BIN:$PATH"

  run zsh "$HELPER" node
  [ "$status" -eq 0 ]
  [ -L "$HOME/.zsh/plugins/node.zsh" ]
}

@test "role with missing required command is skipped with warning" {
  # Use a temp DOTFILES so test fixtures don't leak into the real repo
  local tmp_dotfiles="$BATS_TEST_TMPDIR/dotfiles"
  mkdir -p "$tmp_dotfiles/roles/_test_missing_cmd" "$tmp_dotfiles/zsh"
  cp "$DOTFILES/zsh/tool_packages.zsh" "$tmp_dotfiles/zsh/"
  echo "no_such_command_xyz" > "$tmp_dotfiles/roles/_test_missing_cmd/requires_cmds"
  cat > "$tmp_dotfiles/roles/_test_missing_cmd/setup" <<'SCRIPT'
echo "setup should not run"
SCRIPT

  DOTFILES="$tmp_dotfiles" run zsh "$HELPER" _test_missing_cmd
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required commands"* ]]
  [[ "$output" != *"setup should not run"* ]]
}
