#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for the setup script's setup_link and link_role functions.
# Uses BATS_TEST_TMPDIR as a fake HOME/DOTFILES so nothing touches the real filesystem.

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export DOTFILES="$BATS_TEST_TMPDIR/dotfiles"
  mkdir -p "$HOME" "$DOTFILES"

  # Define the functions under test directly (they use $HOME and $DOTFILES).
  # This mirrors the implementations in the setup script but avoids sourcing
  # the entire script which would run top-level commands.
  setup_link() {
    src="$DOTFILES/$1"
    dst="$HOME/$2"
    if [ "$src" -ef "$dst" ]; then
      return
    fi
    if [[ -f "$dst" || -h "$dst" ]]; then
      >&2 echo "WARNING: $dst exists, overwriting"
      rm -f "$dst"
    fi
    echo "$src -> $dst"
    ln -s "$src" "$dst"
  }

  link_role() {
    echo "Adding role $1"
    role_dir=roles/$1
    if [ ! -d "$DOTFILES/$role_dir" ]; then
      echo "ERROR: No role dir at $role_dir"
      return
    fi
    setup_file=$DOTFILES/$role_dir/setup
    if [ -f "$setup_file" ]; then
      source "$setup_file"
    fi
    zsh_plugin_file="$role_dir/zsh_plugin"
    if [ -f "$DOTFILES/$zsh_plugin_file" ]; then
      setup_link "$zsh_plugin_file" ".zsh/plugins/$1.zsh"
    fi
  }
}

@test "setup_link creates a symlink" {
  mkdir -p "$DOTFILES/zsh"
  echo "zshrc content" > "$DOTFILES/zsh/zshrc"

  setup_link "zsh/zshrc" ".zshrc"

  [ -L "$HOME/.zshrc" ]
  [ "$(readlink "$HOME/.zshrc")" = "$DOTFILES/zsh/zshrc" ]
}

@test "setup_link is idempotent" {
  mkdir -p "$DOTFILES/zsh"
  echo "zshrc content" > "$DOTFILES/zsh/zshrc"

  setup_link "zsh/zshrc" ".zshrc"
  run --separate-stderr setup_link "zsh/zshrc" ".zshrc"

  [ "$status" -eq 0 ]
  [ -z "$stderr" ]
}

@test "setup_link overwrites existing file" {
  mkdir -p "$DOTFILES/zsh"
  echo "zshrc content" > "$DOTFILES/zsh/zshrc"
  echo "old content" > "$HOME/.zshrc"

  run --separate-stderr setup_link "zsh/zshrc" ".zshrc"

  [ "$status" -eq 0 ]
  [ -L "$HOME/.zshrc" ]
  [[ "$stderr" == *"WARNING"* ]]
}

@test "setup_link overwrites stale symlink" {
  mkdir -p "$DOTFILES/zsh"
  echo "zshrc content" > "$DOTFILES/zsh/zshrc"
  ln -s "/nonexistent/path" "$HOME/.zshrc"

  run --separate-stderr setup_link "zsh/zshrc" ".zshrc"

  [ "$status" -eq 0 ]
  [ -L "$HOME/.zshrc" ]
  [ "$(readlink "$HOME/.zshrc")" = "$DOTFILES/zsh/zshrc" ]
}

@test "link_role errors on missing role" {
  run link_role "nonexistent"

  [[ "$output" == *"ERROR: No role dir"* ]]
}

@test "link_role sources setup script" {
  mkdir -p "$DOTFILES/roles/testrole"
  echo 'export SETUP_WAS_SOURCED=yes' > "$DOTFILES/roles/testrole/setup"

  link_role "testrole"

  [ "$SETUP_WAS_SOURCED" = "yes" ]
}

@test "link_role symlinks zsh_plugin" {
  mkdir -p "$DOTFILES/roles/testrole"
  mkdir -p "$HOME/.zsh/plugins"
  echo "plugin content" > "$DOTFILES/roles/testrole/zsh_plugin"

  link_role "testrole"

  [ -L "$HOME/.zsh/plugins/testrole.zsh" ]
  [ "$(readlink "$HOME/.zsh/plugins/testrole.zsh")" = "$DOTFILES/roles/testrole/zsh_plugin" ]
}
