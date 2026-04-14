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

@test "skills directory symlink makes review skill accessible" {
  mkdir -p "$DOTFILES/claude/skills/review"
  echo "# Review" > "$DOTFILES/claude/skills/review/SKILL.md"
  mkdir -p "$HOME/.claude"

  setup_link "claude/skills" ".claude/skills"

  [ -L "$HOME/.claude/skills" ]
  [ -f "$HOME/.claude/skills/review/SKILL.md" ]
}

@test "skills directory symlink makes all skills accessible" {
  mkdir -p "$DOTFILES/claude/skills/review"
  mkdir -p "$DOTFILES/claude/skills/create-spec"
  echo "# Review" > "$DOTFILES/claude/skills/review/SKILL.md"
  echo "# Create Spec" > "$DOTFILES/claude/skills/create-spec/SKILL.md"
  mkdir -p "$HOME/.claude"

  setup_link "claude/skills" ".claude/skills"

  [ -f "$HOME/.claude/skills/review/SKILL.md" ]
  [ -f "$HOME/.claude/skills/create-spec/SKILL.md" ]
}

@test "pi settings symlink points to tracked settings" {
  mkdir -p "$DOTFILES/pi/agent" "$HOME/.pi/agent"
  cat > "$DOTFILES/pi/agent/settings.json" <<'JSON'
{
  "skills": ["~/.claude/skills"]
}
JSON

  setup_link "pi/agent/settings.json" ".pi/agent/settings.json"

  [ -L "$HOME/.pi/agent/settings.json" ]
  [ "$(readlink "$HOME/.pi/agent/settings.json")" = "$DOTFILES/pi/agent/settings.json" ]
  grep -F '"~/.claude/skills"' "$HOME/.pi/agent/settings.json"
}

# --- Scripts → ~/bin symlink tests ---

@test "setup links all scripts into bin, skipping directories" {
  mkdir -p "$DOTFILES/scripts/subdir"
  echo '#!/bin/sh' > "$DOTFILES/scripts/tool-a"
  echo '#!/bin/sh' > "$DOTFILES/scripts/tool-b"
  echo '#!/bin/sh' > "$DOTFILES/scripts/subdir/nested"
  mkdir -p "$HOME/bin"

  for SCRIPT in "$DOTFILES"/scripts/*; do
    [[ -d "$SCRIPT" ]] && continue
    NAME=$(basename "$SCRIPT")
    setup_link "scripts/$NAME" "bin/$NAME"
  done

  [ -L "$HOME/bin/tool-a" ]
  [ -L "$HOME/bin/tool-b" ]
  [ ! -e "$HOME/bin/subdir" ]
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

# --- install_go_tools tests ---

# Set up stubs and source the real install_go_tools function.
# Overrides are injected via MAKE_LIST_OUTPUT, CURL_RESULT, and MAKE_BUILD_RESULT variables.
_define_install_go_tools() {
  DRY_RUN=${DRY_RUN:-0}

  # Stub make: for "list" returns MAKE_LIST_OUTPUT, for "all" uses MAKE_BUILD_RESULT
  make() {
    local dir="" silent=0 target=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -C) dir="$2"; shift 2 ;;
        -s) silent=1; shift ;;
        *) target="$1"; shift ;;
      esac
    done
    case "$target" in
      list) echo "${MAKE_LIST_OUTPUT:-ta}" ;;
      all)  return "${MAKE_BUILD_RESULT:-0}" ;;
      *)    return 0 ;;
    esac
  }

  # Stub curl: succeeds or fails based on CURL_RESULT (default: 1 = fail)
  curl() {
    return "${CURL_RESULT:-1}"
  }

  source "${BATS_TEST_FILENAME%/*}/../lib/install_go_tools.zsh"
}

@test "install_go_tools: up to date when hash matches sidecar" {
  _define_install_go_tools

  # Create a fake tools/go.mod so hash computation works
  mkdir -p "$DOTFILES/tools"
  echo "module dotfiles/tools" > "$DOTFILES/tools/go.mod"

  # Compute what hash the function will compute, then store it
  local expected_hash
  expected_hash=$(cd "$DOTFILES" && find tools -type f \( -name 'go.mod' -o \( -name '*.go' ! -name '*_test.go' \) \) \
      ! -path '*/testdata/*' | sort | xargs shasum | shasum | cut -d' ' -f1)
  expected_hash=${expected_hash:0:16}

  mkdir -p "$HOME/.cache/dotfiles"
  echo "$expected_hash" > "$HOME/.cache/dotfiles/go-build-hash"

  run install_go_tools

  [ "$status" -eq 0 ]
  [[ "$output" == *"Go tools up to date"* ]]
}

@test "install_go_tools: dry-run prints what it would do" {
  DRY_RUN=1
  _define_install_go_tools

  mkdir -p "$DOTFILES/tools"
  echo "module dotfiles/tools" > "$DOTFILES/tools/go.mod"

  run install_go_tools

  [ "$status" -eq 0 ]
  [[ "$output" == *"Would install Go tools"* ]]
  # No sidecar hash written
  [ ! -f "$HOME/.cache/dotfiles/go-build-hash" ]
}

@test "install_go_tools: warns and returns 1 when no binaries and no go" {
  _define_install_go_tools
  export CURL_RESULT=1

  mkdir -p "$DOTFILES/tools"
  echo "module dotfiles/tools" > "$DOTFILES/tools/go.mod"
  mkdir -p "$HOME/bin"

  # Build a restricted PATH with tools needed by install_go_tools but excluding 'go'
  local fakepath="$BATS_TEST_TMPDIR/fakepath"
  mkdir -p "$fakepath"
  for cmd in find sort xargs shasum cut tr uname mktemp mkdir cat rm chmod mv; do
    local cmd_path
    cmd_path=$(command -v "$cmd" 2>/dev/null) || continue
    ln -sf "$cmd_path" "$fakepath/$cmd" 2>/dev/null || true
  done
  export PATH="$fakepath"

  run install_go_tools

  [ "$status" -eq 1 ]
  [[ "$output" == *"WARNING"* ]]
  [[ "$output" == *"Go not installed"* ]]
  # No sidecar hash written
  [ ! -f "$HOME/.cache/dotfiles/go-build-hash" ]
}
