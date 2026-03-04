#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for _brew_drift (Brewfile drift detection).

HELPER="${BATS_TEST_FILENAME%/*}/helpers/brew_drift.zsh"

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export DOTFILES="$(cd "${BATS_TEST_FILENAME%/*}/.." && pwd)"
  export MOCK_BIN="$BATS_TEST_TMPDIR/mock_bin"
  export BREW_MOCK_DIR="$BATS_TEST_TMPDIR/brew_data"
  mkdir -p "$HOME" "$MOCK_BIN" "$BREW_MOCK_DIR"

  # Create a mock Brewfile (referenced by --global)
  touch "$HOME/.Brewfile"

  # Create mock brew that returns data from files
  cat > "$MOCK_BIN/brew" <<'SCRIPT'
#!/bin/bash
case "$*" in
  "bundle list --global --formula")
    cat "$BREW_MOCK_DIR/bundle-list-formula" 2>/dev/null ;;
  "bundle list --global --cask")
    cat "$BREW_MOCK_DIR/bundle-list-cask" 2>/dev/null ;;
  "list --installed-on-request --formula")
    cat "$BREW_MOCK_DIR/list-on-request" 2>/dev/null ;;
  "list --installed-as-dependency --formula")
    cat "$BREW_MOCK_DIR/list-as-dep" 2>/dev/null ;;
  "list --cask")
    cat "$BREW_MOCK_DIR/list-cask" 2>/dev/null ;;
  "bundle check --global --verbose")
    cat "$BREW_MOCK_DIR/bundle-check" 2>/dev/null
    exit "${BREW_CHECK_EXIT:-0}" ;;
  *)
    echo "mock brew: unexpected args: $*" >&2
    exit 1 ;;
esac
SCRIPT
  chmod +x "$MOCK_BIN/brew"
}

# --- Display mode tests ---

@test "brew-drift: all in sync" {
  # Brewfile has git and jq, both installed on-request, neither as-dep
  echo -e "git\njq" > "$BREW_MOCK_DIR/bundle-list-formula"
  echo "alfred" > "$BREW_MOCK_DIR/bundle-list-cask"
  echo -e "git\njq" > "$BREW_MOCK_DIR/list-on-request"
  : > "$BREW_MOCK_DIR/list-as-dep"
  echo "alfred" > "$BREW_MOCK_DIR/list-cask"
  : > "$BREW_MOCK_DIR/bundle-check"

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" == *"Brewfile is in sync"* ]]
}

@test "brew-drift: untracked formula detected" {
  echo "git" > "$BREW_MOCK_DIR/bundle-list-formula"
  : > "$BREW_MOCK_DIR/bundle-list-cask"
  # vim installed on-request but not in Brewfile
  echo -e "git\nvim" > "$BREW_MOCK_DIR/list-on-request"
  : > "$BREW_MOCK_DIR/list-as-dep"
  : > "$BREW_MOCK_DIR/list-cask"
  : > "$BREW_MOCK_DIR/bundle-check"

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" == *"Untracked formulae"* ]]
  [[ "$output" == *"vim"* ]]
  [[ "$output" != *"git"* ]]
}

@test "brew-drift: dependency formulae excluded from untracked" {
  echo "ffmpeg" > "$BREW_MOCK_DIR/bundle-list-formula"
  : > "$BREW_MOCK_DIR/bundle-list-cask"
  # libx264 shows as on-request AND as-dep (orphaned dep scenario)
  echo -e "ffmpeg\nlibx264" > "$BREW_MOCK_DIR/list-on-request"
  echo "libx264" > "$BREW_MOCK_DIR/list-as-dep"
  : > "$BREW_MOCK_DIR/list-cask"
  : > "$BREW_MOCK_DIR/bundle-check"

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" == *"Brewfile is in sync"* ]]
  [[ "$output" != *"libx264"* ]]
}

@test "brew-drift: untracked cask detected" {
  : > "$BREW_MOCK_DIR/bundle-list-formula"
  echo "alfred" > "$BREW_MOCK_DIR/bundle-list-cask"
  : > "$BREW_MOCK_DIR/list-on-request"
  : > "$BREW_MOCK_DIR/list-as-dep"
  # raycast installed but not in Brewfile
  echo -e "alfred\nraycast" > "$BREW_MOCK_DIR/list-cask"
  : > "$BREW_MOCK_DIR/bundle-check"

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" == *"Untracked casks"* ]]
  [[ "$output" == *"raycast"* ]]
  [[ "$output" != *"alfred"* ]]
}

@test "brew-drift: missing packages detected" {
  echo "git" > "$BREW_MOCK_DIR/bundle-list-formula"
  : > "$BREW_MOCK_DIR/bundle-list-cask"
  echo "git" > "$BREW_MOCK_DIR/list-on-request"
  : > "$BREW_MOCK_DIR/list-as-dep"
  : > "$BREW_MOCK_DIR/list-cask"
  cat > "$BREW_MOCK_DIR/bundle-check" <<'EOF'
brew bundle can't satisfy your Brewfile's dependencies.
→ Formula jq needs to be installed or updated.
Satisfy missing dependencies with `brew bundle install`.
EOF
  export BREW_CHECK_EXIT=1

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" == *"Missing"* ]]
  [[ "$output" == *"Formula jq needs to be installed"* ]]
  # Header/footer lines should be stripped
  [[ "$output" != *"can't satisfy"* ]]
  [[ "$output" != *"Satisfy missing"* ]]
}

@test "brew-drift: mixed untracked and missing" {
  echo "git" > "$BREW_MOCK_DIR/bundle-list-formula"
  : > "$BREW_MOCK_DIR/bundle-list-cask"
  echo -e "git\nvim" > "$BREW_MOCK_DIR/list-on-request"
  : > "$BREW_MOCK_DIR/list-as-dep"
  echo "raycast" > "$BREW_MOCK_DIR/list-cask"
  cat > "$BREW_MOCK_DIR/bundle-check" <<'EOF'
brew bundle can't satisfy your Brewfile's dependencies.
→ Formula jq needs to be installed or updated.
Satisfy missing dependencies with `brew bundle install`.
EOF
  export BREW_CHECK_EXIT=1

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" == *"Untracked formulae"* ]]
  [[ "$output" == *"vim"* ]]
  [[ "$output" == *"Untracked casks"* ]]
  [[ "$output" == *"raycast"* ]]
  [[ "$output" == *"Missing"* ]]
  [[ "$output" == *"Formula jq"* ]]
  [[ "$output" == *"brew-drift --fix"* ]]
}

@test "brew-drift: footer suggests --fix when drift exists" {
  : > "$BREW_MOCK_DIR/bundle-list-formula"
  : > "$BREW_MOCK_DIR/bundle-list-cask"
  echo "vim" > "$BREW_MOCK_DIR/list-on-request"
  : > "$BREW_MOCK_DIR/list-as-dep"
  : > "$BREW_MOCK_DIR/list-cask"
  : > "$BREW_MOCK_DIR/bundle-check"

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" == *"brew-drift --fix"* ]]
}

@test "brew-drift: no footer when in sync" {
  : > "$BREW_MOCK_DIR/bundle-list-formula"
  : > "$BREW_MOCK_DIR/bundle-list-cask"
  : > "$BREW_MOCK_DIR/list-on-request"
  : > "$BREW_MOCK_DIR/list-as-dep"
  : > "$BREW_MOCK_DIR/list-cask"
  : > "$BREW_MOCK_DIR/bundle-check"

  run zsh "$HELPER"

  [ "$status" -eq 0 ]
  [[ "$output" != *"--fix"* ]]
}
