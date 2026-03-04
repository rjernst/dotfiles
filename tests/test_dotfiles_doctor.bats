#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for dotfiles-doctor plugin check functions.

HELPER="${BATS_TEST_FILENAME%/*}/helpers/dotfiles_doctor.zsh"

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export DOTFILES="$BATS_TEST_TMPDIR/dotfiles"
  export ZDOTDIR="$HOME/.zsh"
  export MOCK_BIN="$BATS_TEST_TMPDIR/mock_bin"
  mkdir -p "$HOME/.ssh" "$HOME/.zsh/plugins" "$HOME/.vim" "$HOME/.git"
  mkdir -p "$HOME/.gradle" "$HOME/.config/gh" "$HOME/.claude"
  mkdir -p "$HOME/.codex/rules"
  mkdir -p "$DOTFILES/zsh/plugins" "$DOTFILES/vim" "$DOTFILES/git"
  mkdir -p "$DOTFILES/gradle" "$DOTFILES/ssh" "$DOTFILES/starship"
  mkdir -p "$DOTFILES/gh" "$DOTFILES/claude" "$DOTFILES/codex/rules"
  mkdir -p "$MOCK_BIN"

  # Copy the doctor plugin into the fake dotfiles
  cp "$(cd "${BATS_TEST_FILENAME%/*}/.." && pwd)/zsh/plugins/dotfiles-doctor.zsh" \
     "$DOTFILES/zsh/plugins/dotfiles-doctor.zsh"

  # Create source files that setup would link
  touch "$DOTFILES/zsh/zshrc"
  touch "$DOTFILES/gradle/properties"
  touch "$DOTFILES/ssh/config"
  touch "$DOTFILES/git/config"
  touch "$DOTFILES/git/ignore"
  touch "$DOTFILES/starship/starship.toml"
  touch "$DOTFILES/gh/config.yml"
  touch "$DOTFILES/claude/CLAUDE.md"
  touch "$DOTFILES/codex/config.toml"
  touch "$DOTFILES/codex/rules/default.rules"
}

# --- Symlink checks ---

@test "doctor symlinks: valid symlink passes" {
  ln -s "$DOTFILES/zsh/zshrc" "$HOME/.zshrc"
  ln -s "$DOTFILES/gradle/properties" "$HOME/.gradle/gradle.properties"
  ln -s "$DOTFILES/ssh/config" "$HOME/.ssh/config"
  ln -s "$DOTFILES/git/config" "$HOME/.gitconfig"
  ln -s "$DOTFILES/git/ignore" "$HOME/.git/ignore"
  ln -s "$DOTFILES/starship/starship.toml" "$HOME/.config/starship.toml"
  ln -s "$DOTFILES/gh/config.yml" "$HOME/.config/gh/config.yml"
  ln -s "$DOTFILES/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
  ln -s "$DOTFILES/codex/config.toml" "$HOME/.codex/config.toml"
  ln -s "$DOTFILES/codex/rules/default.rules" "$HOME/.codex/rules/default.rules"

  run zsh "$HELPER" _doctor_check_symlinks

  [ "$status" -eq 0 ]
  [[ "$output" == *"ERRORS=0"* ]]
  [[ "$output" == *"WARNINGS=0"* ]]
}

@test "doctor symlinks: missing symlink fails" {
  # Don't create any symlinks — all should fail
  run zsh "$HELPER" _doctor_check_symlinks

  [ "$status" -eq 0 ]
  [[ "$output" == *"FAIL"* ]]
  [[ "$output" == *"missing"* ]]
  [[ "$output" != *"ERRORS=0"* ]]
}

@test "doctor symlinks: wrong target fails" {
  ln -s "/wrong/path" "$HOME/.zshrc"

  run zsh "$HELPER" _doctor_check_symlinks

  [ "$status" -eq 0 ]
  [[ "$output" == *"FAIL"* ]]
  [[ "$output" == *"expected"* ]]
}

@test "doctor symlinks: regular file warns" {
  echo "not a symlink" > "$HOME/.zshrc"

  run zsh "$HELPER" _doctor_check_symlinks

  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [[ "$output" == *"not a symlink"* ]]
}

@test "doctor symlinks: vim files checked dynamically" {
  touch "$DOTFILES/vim/vimrc"
  ln -s "$DOTFILES/vim/vimrc" "$HOME/.vim/vimrc"

  # Create all expected symlinks so vim check is the relevant part
  ln -s "$DOTFILES/zsh/zshrc" "$HOME/.zshrc"
  ln -s "$DOTFILES/gradle/properties" "$HOME/.gradle/gradle.properties"
  ln -s "$DOTFILES/ssh/config" "$HOME/.ssh/config"
  ln -s "$DOTFILES/git/config" "$HOME/.gitconfig"
  ln -s "$DOTFILES/git/ignore" "$HOME/.git/ignore"
  ln -s "$DOTFILES/starship/starship.toml" "$HOME/.config/starship.toml"
  ln -s "$DOTFILES/gh/config.yml" "$HOME/.config/gh/config.yml"
  ln -s "$DOTFILES/claude/CLAUDE.md" "$HOME/.claude/CLAUDE.md"
  ln -s "$DOTFILES/codex/config.toml" "$HOME/.codex/config.toml"
  ln -s "$DOTFILES/codex/rules/default.rules" "$HOME/.codex/rules/default.rules"

  run zsh "$HELPER" _doctor_check_symlinks

  [ "$status" -eq 0 ]
  [[ "$output" == *"OK"*"$HOME/.vim/vimrc"* ]]
  [[ "$output" == *"ERRORS=0"* ]]
}

# --- Stale plugin checks ---

@test "doctor plugins: no stale symlinks passes" {
  touch "$DOTFILES/zsh/plugins/test.zsh"
  ln -s "$DOTFILES/zsh/plugins/test.zsh" "$ZDOTDIR/plugins/test.zsh"

  run zsh "$HELPER" _doctor_check_stale_plugins

  [ "$status" -eq 0 ]
  [[ "$output" == *"ERRORS=0"* ]]
  [[ "$output" == *"no stale"* ]]
}

@test "doctor plugins: stale symlink fails" {
  ln -s "/nonexistent/plugin.zsh" "$ZDOTDIR/plugins/stale.zsh"

  run zsh "$HELPER" _doctor_check_stale_plugins

  [ "$status" -eq 0 ]
  [[ "$output" == *"FAIL"* ]]
  [[ "$output" == *"stale"* ]]
  [[ "$output" == *"ERRORS=1"* ]]
}

# --- Roles checks ---

@test "doctor roles: valid roles pass" {
  mkdir -p "$DOTFILES/hosts/testhost/." "$DOTFILES/roles/git"
  echo "git" > "$DOTFILES/hosts/testhost/roles"

  # Override hostname
  run zsh -c "
    hostname() { echo testhost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_roles
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"OK"*"role git"* ]]
  [[ "$output" == *"ERRORS=0"* ]]
}

@test "doctor roles: missing role directory fails" {
  mkdir -p "$DOTFILES/hosts/testhost"
  echo "nonexistent" > "$DOTFILES/hosts/testhost/roles"

  run zsh -c "
    hostname() { echo testhost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_roles
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"FAIL"* ]]
  [[ "$output" == *"ERRORS=1"* ]]
}

@test "doctor roles: no roles file warns" {
  # No hosts directory at all
  run zsh -c "
    hostname() { echo nohost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_roles
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [[ "$output" == *"WARNINGS=1"* ]]
}

# --- SSH key checks ---

@test "doctor ssh: keys present passes" {
  touch "$HOME/.ssh/id_ed25519" "$HOME/.ssh/id_ed25519.pub"
  touch "$HOME/.ssh/github_ed25519" "$HOME/.ssh/github_ed25519.pub"

  # Mock ssh-add to report keys
  cat > "$MOCK_BIN/ssh-add" <<'SCRIPT'
#!/bin/bash
echo "256 SHA256:abc ed25519"
SCRIPT
  chmod +x "$MOCK_BIN/ssh-add"

  run zsh -c "
    export PATH='$MOCK_BIN:\$PATH'
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_ssh_keys
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"ERRORS=0"* ]]
}

@test "doctor ssh: missing keys fails" {
  # No keys at all
  cat > "$MOCK_BIN/ssh-add" <<'SCRIPT'
#!/bin/bash
echo "The agent has no identities."
exit 1
SCRIPT
  chmod +x "$MOCK_BIN/ssh-add"

  run zsh -c "
    export PATH='$MOCK_BIN:\$PATH'
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_ssh_keys
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"FAIL"* ]]
  # 4 key files missing (2 private + 2 public)
  [[ "$output" == *"ERRORS=4"* ]]
}

# --- Git signing checks ---

@test "doctor git-signing: configured passes" {
  touch "$HOME/.git/user.config"
  touch "$HOME/.ssh/signing_key.pub"

  # Mock git config to return signing info
  cat > "$MOCK_BIN/git" <<SCRIPT
#!/bin/bash
if [[ "\$*" == *"user.signingKey"* ]]; then
  echo "$HOME/.ssh/signing_key.pub"
elif [[ "\$*" == *"gpg.ssh.allowedSignersFile"* ]]; then
  echo "$HOME/.ssh/allowed_signers"
fi
SCRIPT
  chmod +x "$MOCK_BIN/git"
  touch "$HOME/.ssh/allowed_signers"

  run zsh -c "
    export PATH='$MOCK_BIN:\$PATH'
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_git_signing
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"ERRORS=0"* ]]
}

@test "doctor git-signing: missing user.config fails" {
  # No user.config, mock git returns empty
  cat > "$MOCK_BIN/git" <<'SCRIPT'
#!/bin/bash
exit 1
SCRIPT
  chmod +x "$MOCK_BIN/git"

  run zsh -c "
    export PATH='$MOCK_BIN:\$PATH'
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_git_signing
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"FAIL"* ]]
  [[ "$output" == *"user.config missing"* ]]
}

# --- Freshness checks ---

@test "doctor freshness: up to date passes" {
  mkdir -p "$ZDOTDIR/var"

  # Mock git to report 0 commits behind
  cat > "$MOCK_BIN/git" <<'SCRIPT'
#!/bin/bash
echo "0"
SCRIPT
  chmod +x "$MOCK_BIN/git"

  run zsh -c "
    export PATH='$MOCK_BIN:\$PATH'
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_freshness
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"up to date"* ]]
  [[ "$output" == *"ERRORS=0"* ]]
  [[ "$output" == *"WARNINGS=0"* ]]
}

@test "doctor freshness: behind warns" {
  mkdir -p "$ZDOTDIR/var"
  echo "5" > "$ZDOTDIR/var/dotfiles-behind-count"

  run zsh -c "
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_freshness
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"5 commit(s) behind"* ]]
  [[ "$output" == *"WARNINGS=1"* ]]
}

# --- Homebrew drift checks ---

@test "doctor brew-drift: in sync passes" {
  mkdir -p "$ZDOTDIR/var"

  cat > "$MOCK_BIN/brew" <<'SCRIPT'
#!/bin/bash
case "$*" in
  "bundle list --global --formula") echo "git" ;;
  "bundle list --global --cask") echo "alfred" ;;
  "list --installed-on-request --formula") echo "git" ;;
  "list --installed-as-dependency --formula") echo "" ;;
  "list --cask") echo "alfred" ;;
  "bundle check --global --verbose") exit 0 ;;
  list\ --formula\ *|list\ --cask\ *) exit 0 ;;
esac
SCRIPT
  chmod +x "$MOCK_BIN/brew"

  run zsh -c "
    export OSTYPE=darwin
    export PATH='$MOCK_BIN:\$PATH'
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_brew_drift
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"OK"*"Brewfile is in sync"* ]]
  [[ "$output" == *"ERRORS=0"* ]]
  [[ "$output" == *"WARNINGS=0"* ]]
}

@test "doctor brew-drift: drift warns with counts" {
  mkdir -p "$ZDOTDIR/var"

  cat > "$MOCK_BIN/brew" <<'SCRIPT'
#!/bin/bash
case "$*" in
  "bundle list --global --formula") echo "git" ;;
  "bundle list --global --cask") echo "" ;;
  "list --installed-on-request --formula") printf "git\nvim\nshellcheck\n" ;;
  "list --installed-as-dependency --formula") echo "" ;;
  "list --cask") echo "raycast" ;;
  "bundle check --global --verbose")
    echo "→ Formula jq needs to be installed or updated."
    exit 1 ;;
  "list --formula jq") exit 1 ;;
  list\ --formula\ *|list\ --cask\ *) exit 0 ;;
esac
SCRIPT
  chmod +x "$MOCK_BIN/brew"

  run zsh -c "
    export OSTYPE=darwin
    export PATH='$MOCK_BIN:\$PATH'
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_brew_drift
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"* ]]
  [[ "$output" == *"3 untracked"* ]]
  [[ "$output" == *"1 missing"* ]]
  [[ "$output" == *"WARNINGS=1"* ]]
}

@test "doctor brew-drift: skipped on non-macOS" {
  run zsh -c "
    export OSTYPE=linux-gnu
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-doctor.zsh'
    local _doctor_errors=0 _doctor_warnings=0
    _doctor_check_brew_drift
    echo ERRORS=\$_doctor_errors
    echo WARNINGS=\$_doctor_warnings
  "

  [ "$status" -eq 0 ]
  [[ "$output" != *"Homebrew"* ]]
  [[ "$output" == *"ERRORS=0"* ]]
  [[ "$output" == *"WARNINGS=0"* ]]
}
