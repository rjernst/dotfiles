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
  DRY_RUN=${DRY_RUN:-0}

  setup_link() {
    src="$DOTFILES/$1"
    dst="$HOME/$2"
    if [ "$src" -ef "$dst" ]; then
      return
    fi
    if [[ -f "$dst" || -h "$dst" ]]; then
      >&2 echo "WARNING: $dst exists, overwriting"
      if (( DRY_RUN )); then
        echo "[dry-run] rm -f $dst"
      else
        rm -f "$dst"
      fi
    fi
    if (( DRY_RUN )); then
      echo "[dry-run] $src -> $dst"
    else
      echo "$src -> $dst"
      ln -s "$src" "$dst"
    fi
  }

  link_role() {
    if (( DRY_RUN )); then
      echo "[dry-run] Adding role $1"
    else
      echo "Adding role $1"
    fi
    role_dir=roles/$1
    if [ ! -d "$DOTFILES/$role_dir" ]; then
      echo "ERROR: No role dir at $role_dir"
      return
    fi
    install_file=$DOTFILES/$role_dir/install
    if [ -f "$install_file" ]; then
      if (( DRY_RUN )); then
        echo "[dry-run] would run install for $1"
      else
        source "$install_file"
      fi
    fi
    setup_file=$DOTFILES/$role_dir/setup
    if [ -f "$setup_file" ]; then
      if (( DRY_RUN )); then
        echo "[dry-run] would run setup for $1"
      else
        source "$setup_file"
      fi
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

@test "link_role sources install script" {
  mkdir -p "$DOTFILES/roles/testrole"
  echo 'export INSTALL_WAS_SOURCED=yes' > "$DOTFILES/roles/testrole/install"

  link_role "testrole"

  [ "$INSTALL_WAS_SOURCED" = "yes" ]
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

# --- Dependency resolution tests (via zsh helper) ---

RESOLVE_HELPER="${BATS_TEST_FILENAME%/*}/helpers/resolve_roles.zsh"

@test "resolve_role: dependency linked before dependent" {
  mkdir -p "$DOTFILES/roles/alpha" "$DOTFILES/roles/beta"
  echo "beta" > "$DOTFILES/roles/alpha/requires"

  run zsh "$RESOLVE_HELPER" alpha
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "beta" ]
  [ "${lines[1]}" = "alpha" ]
}

@test "resolve_role: transitive dependencies" {
  mkdir -p "$DOTFILES/roles/a" "$DOTFILES/roles/b" "$DOTFILES/roles/c"
  echo "b" > "$DOTFILES/roles/a/requires"
  echo "c" > "$DOTFILES/roles/b/requires"

  run zsh "$RESOLVE_HELPER" a
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "c" ]
  [ "${lines[1]}" = "b" ]
  [ "${lines[2]}" = "a" ]
}

@test "resolve_role: de-duplication of shared dependency" {
  mkdir -p "$DOTFILES/roles/x" "$DOTFILES/roles/y" "$DOTFILES/roles/shared"
  echo "shared" > "$DOTFILES/roles/x/requires"
  echo "shared" > "$DOTFILES/roles/y/requires"

  run zsh "$RESOLVE_HELPER" x y
  [ "$status" -eq 0 ]
  # shared appears exactly once
  local count=0
  for line in "${lines[@]}"; do
    if [ "$line" = "shared" ]; then
      count=$((count + 1))
    fi
  done
  [ "$count" -eq 1 ]
  [ "${#lines[@]}" -eq 3 ]
}

@test "resolve_role: cycle detection" {
  mkdir -p "$DOTFILES/roles/p" "$DOTFILES/roles/q"
  echo "q" > "$DOTFILES/roles/p/requires"
  echo "p" > "$DOTFILES/roles/q/requires"

  run zsh "$RESOLVE_HELPER" p
  [ "$status" -ne 0 ]
  [[ "$output" == *"cycle"* ]]
}

@test "resolve_role: missing dependency role" {
  mkdir -p "$DOTFILES/roles/has_dep"
  echo "nonexistent" > "$DOTFILES/roles/has_dep/requires"

  run zsh "$RESOLVE_HELPER" has_dep
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found"* ]]
}

@test "resolve_role: role with no requires file" {
  mkdir -p "$DOTFILES/roles/standalone"

  run zsh "$RESOLVE_HELPER" standalone
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "standalone" ]
  [ "${#lines[@]}" -eq 1 ]
}

# --- Skills symlink tests ---

@test "skills directory symlink makes code-review skill accessible" {
  mkdir -p "$DOTFILES/claude/skills/code-review"
  echo "# Code Review" > "$DOTFILES/claude/skills/code-review/SKILL.md"
  mkdir -p "$HOME/.claude"

  setup_link "claude/skills" ".claude/skills"

  [ -L "$HOME/.claude/skills" ]
  [ -f "$HOME/.claude/skills/code-review/SKILL.md" ]
}

@test "skills directory symlink makes all skills accessible" {
  mkdir -p "$DOTFILES/claude/skills/code-review"
  mkdir -p "$DOTFILES/claude/skills/create-spec"
  echo "# Code Review" > "$DOTFILES/claude/skills/code-review/SKILL.md"
  echo "# Create Spec" > "$DOTFILES/claude/skills/create-spec/SKILL.md"
  mkdir -p "$HOME/.claude"

  setup_link "claude/skills" ".claude/skills"

  [ -f "$HOME/.claude/skills/code-review/SKILL.md" ]
  [ -f "$HOME/.claude/skills/create-spec/SKILL.md" ]
}

# --- Dry-run mode tests ---

@test "setup_link dry-run does not create symlink" {
  DRY_RUN=1
  mkdir -p "$DOTFILES/zsh"
  echo "zshrc content" > "$DOTFILES/zsh/zshrc"

  run setup_link "zsh/zshrc" ".zshrc"

  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.zshrc" ]
  [[ "$output" == *"[dry-run]"* ]]
}

@test "setup_link dry-run does not remove existing file" {
  DRY_RUN=1
  mkdir -p "$DOTFILES/zsh"
  echo "zshrc content" > "$DOTFILES/zsh/zshrc"
  echo "old content" > "$HOME/.zshrc"

  run --separate-stderr setup_link "zsh/zshrc" ".zshrc"

  [ "$status" -eq 0 ]
  # original file should still exist (not removed)
  [ -f "$HOME/.zshrc" ]
  [ "$(cat "$HOME/.zshrc")" = "old content" ]
  [[ "$output" == *"[dry-run] rm -f"* ]]
}

@test "link_role dry-run skips install and setup scripts" {
  DRY_RUN=1
  mkdir -p "$DOTFILES/roles/testrole"
  echo 'export INSTALL_WAS_SOURCED=yes' > "$DOTFILES/roles/testrole/install"
  echo 'export SETUP_WAS_SOURCED=yes' > "$DOTFILES/roles/testrole/setup"

  run link_role "testrole"

  [ "$status" -eq 0 ]
  # scripts should NOT have been sourced
  [ -z "${INSTALL_WAS_SOURCED:-}" ]
  [ -z "${SETUP_WAS_SOURCED:-}" ]
  [[ "$output" == *"[dry-run] would run install for testrole"* ]]
  [[ "$output" == *"[dry-run] would run setup for testrole"* ]]
}
