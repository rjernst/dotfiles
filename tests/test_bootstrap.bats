#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for bootstrap functions.
# Sources the bootstrap script (the source guard prevents main execution)
# and tests individual functions with mocked externals.

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export DOTFILES="$BATS_TEST_TMPDIR/dotfiles"
  mkdir -p "$HOME" "$DOTFILES"

  # Put a mock bin directory first on PATH for stubs
  export MOCK_BIN="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_BIN"
  export PATH="$MOCK_BIN:$PATH"

  source "${BATS_TEST_FILENAME%/*}/../bootstrap"
}

# --- identify_platform ---

@test "identify_platform returns a known platform" {
  run identify_platform
  [ "$status" -eq 0 ]
  [[ "$output" == "macos" || "$output" == "arch" || "$output" == "unknown" ]]
}

# --- install_package ---

@test "install_package skips when command exists" {
  # 'ls' always exists
  platform_install() { echo "SHOULD NOT BE CALLED"; return 1; }
  export -f platform_install

  run install_package ls
  [ "$status" -eq 0 ]
  [[ "$output" != *"SHOULD NOT BE CALLED"* ]]
}

@test "install_package calls platform_install when command missing" {
  platform_install() { echo "installing $1"; }
  export -f platform_install

  run install_package nonexistent_command_xyz
  [ "$status" -eq 0 ]
  [[ "$output" == *"installing nonexistent_command_xyz"* ]]
}

# --- check_ssh_keys ---

@test "check_ssh_keys creates .ssh directory" {
  # Stub ssh-keygen to avoid real key generation
  cat > "$MOCK_BIN/ssh-keygen" <<'SCRIPT'
#!/bin/bash
touch "$2"
touch "${2}.pub"
SCRIPT
  chmod +x "$MOCK_BIN/ssh-keygen"

  check_ssh_keys

  [ -d "$HOME/.ssh" ]
}

@test "check_ssh_keys sets permissions to 700" {
  cat > "$MOCK_BIN/ssh-keygen" <<'SCRIPT'
#!/bin/bash
touch "$2"
touch "${2}.pub"
SCRIPT
  chmod +x "$MOCK_BIN/ssh-keygen"

  check_ssh_keys

  local perms
  perms=$(stat -f '%Lp' "$HOME/.ssh" 2>/dev/null || stat -c '%a' "$HOME/.ssh" 2>/dev/null)
  [ "$perms" = "700" ]
}

@test "check_ssh_keys calls ssh-keygen for missing keys" {
  cat > "$MOCK_BIN/ssh-keygen" <<'SCRIPT'
#!/bin/bash
echo "keygen:$2" >> "$HOME/.ssh/keygen.log"
touch "$2"
touch "${2}.pub"
SCRIPT
  chmod +x "$MOCK_BIN/ssh-keygen"

  check_ssh_keys

  [ -f "$HOME/.ssh/keygen.log" ]
  grep -q "id_ed25519" "$HOME/.ssh/keygen.log"
  grep -q "github_ed25519" "$HOME/.ssh/keygen.log"
}

# --- ensure_ssh_key_exists ---

@test "ensure_ssh_key_exists skips existing key" {
  mkdir -p "$HOME/.ssh"
  touch "$HOME/.ssh/testkey"
  ssh_dir="$HOME/.ssh"

  cat > "$MOCK_BIN/ssh-keygen" <<'SCRIPT'
#!/bin/bash
echo "SHOULD NOT BE CALLED"
exit 1
SCRIPT
  chmod +x "$MOCK_BIN/ssh-keygen"

  run ensure_ssh_key_exists testkey
  [ "$status" -eq 0 ]
  [[ "$output" != *"SHOULD NOT BE CALLED"* ]]
}

@test "ensure_ssh_key_exists generates missing key" {
  mkdir -p "$HOME/.ssh"
  ssh_dir="$HOME/.ssh"

  cat > "$MOCK_BIN/ssh-keygen" <<'SCRIPT'
#!/bin/bash
touch "$2"
SCRIPT
  chmod +x "$MOCK_BIN/ssh-keygen"

  run ensure_ssh_key_exists newkey
  [ "$status" -eq 0 ]
  [[ "$output" == *"creating"* ]]
}

# --- clone_dotfiles ---

@test "clone_dotfiles skips when DOTFILES dir exists" {
  mkdir -p "$DOTFILES"

  run clone_dotfiles
  [ "$status" -eq 0 ]
  [[ "$output" == *"exists, skipping clone"* ]]
}
