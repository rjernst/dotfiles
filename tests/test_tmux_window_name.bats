#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/tmux-window-name

setup() {
  SCRIPT="${BATS_TEST_FILENAME%/*}/../scripts/tmux-window-name"

  # Create a temp directory for test git repos
  TEST_DIR="$BATS_TEST_TMPDIR/repos"
  mkdir -p "$TEST_DIR"

  # Disable gpg signing for test repos
  export GIT_CONFIG_GLOBAL="$BATS_TEST_TMPDIR/gitconfig"
  git config --global commit.gpgsign false
  git config --global tag.gpgsign false
}

# Detect available shell (zsh preferred, bash fallback)
if command -v zsh &>/dev/null; then
  SHELL_CMD=zsh
else
  SHELL_CMD=bash
fi

# Helper: create a git repo on a given branch
create_repo() {
  local name="$1" branch="${2:-main}"
  local repo_path="$TEST_DIR/$name"
  mkdir -p "$repo_path"
  git -C "$repo_path" init -b "$branch" --quiet
  git -C "$repo_path" config user.email "test@test.com"
  git -C "$repo_path" config user.name "Test"
  touch "$repo_path/.gitkeep"
  git -C "$repo_path" add .gitkeep
  git -C "$repo_path" commit -m "init" --quiet
  echo "$repo_path"
}

@test "git repo on main shows just repo name" {
  repo_path=$(create_repo "myproject" "main")
  run "$SHELL_CMD" "$SCRIPT" "$repo_path"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "myproject" ]]
}

@test "git repo on master shows just repo name" {
  repo_path=$(create_repo "masterproject" "master")
  run "$SHELL_CMD" "$SCRIPT" "$repo_path"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "masterproject" ]]
}

@test "git repo on feature branch shows repo [branch]" {
  repo_path=$(create_repo "myproject2" "main")
  git -C "$repo_path" checkout -b "my-feature" --quiet
  run "$SHELL_CMD" "$SCRIPT" "$repo_path"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "myproject2 [my-feature]" ]]
}

@test "strips feature/ prefix from branch name" {
  repo_path=$(create_repo "myproject3" "main")
  git -C "$repo_path" checkout -b "feature/cool-thing" --quiet
  run "$SHELL_CMD" "$SCRIPT" "$repo_path"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "myproject3 [cool-thing]" ]]
}

@test "strips bugfix/ prefix from branch name" {
  repo_path=$(create_repo "myproject4" "main")
  git -C "$repo_path" checkout -b "bugfix/fix-it" --quiet
  run "$SHELL_CMD" "$SCRIPT" "$repo_path"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "myproject4 [fix-it]" ]]
}

@test "strips hotfix/ prefix from branch name" {
  repo_path=$(create_repo "myproject5" "main")
  git -C "$repo_path" checkout -b "hotfix/urgent" --quiet
  run "$SHELL_CMD" "$SCRIPT" "$repo_path"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "myproject5 [urgent]" ]]
}

@test "non-git directory shows basename" {
  local dir="$TEST_DIR/not-a-repo"
  mkdir -p "$dir"
  run "$SHELL_CMD" "$SCRIPT" "$dir"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "not-a-repo" ]]
}

@test "no argument defaults to current directory" {
  run "$SHELL_CMD" "$SCRIPT"
  [[ "$status" -eq 0 ]]
  [[ -n "$output" ]]
}

@test "subdirectory of git repo shows repo name" {
  repo_path=$(create_repo "myproject6" "main")
  mkdir -p "$repo_path/src/deep"
  run "$SHELL_CMD" "$SCRIPT" "$repo_path/src/deep"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "myproject6" ]]
}

@test "nonexistent path shows basename" {
  run "$SHELL_CMD" "$SCRIPT" "/tmp/does-not-exist-$$"
  [[ "$status" -eq 0 ]]
  [[ "$output" == "does-not-exist-$$" ]]
}
