#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/git-make-worktree
# Uses temp git repos to simulate a project with an upstream remote.

setup() {
  SCRIPT="${BATS_TEST_FILENAME%/*}/../scripts/git-make-worktree"

  export PROJECT="$BATS_TEST_TMPDIR/project"
  export GIT_AUTHOR_NAME="Test"
  export GIT_AUTHOR_EMAIL="test@test.com"
  export GIT_COMMITTER_NAME="Test"
  export GIT_COMMITTER_EMAIL="test@test.com"

  # Create an upstream bare repo
  git init --bare "$BATS_TEST_TMPDIR/upstream.git"

  # Create project repo with an upstream remote and push a branch
  git init "$PROJECT"
  git -C "$PROJECT" commit --allow-empty -m "initial"
  git -C "$PROJECT" remote add upstream "$BATS_TEST_TMPDIR/upstream.git"
  git -C "$PROJECT" push upstream HEAD:main
  git -C "$PROJECT" push upstream HEAD:feature-branch
}

@test "fails with no arguments" {
  cd "$PROJECT"
  run zsh "$SCRIPT"

  [ "$status" -eq 1 ]
  [[ "$output" == *"usage:"* ]]
}

@test "fails with one argument" {
  cd "$PROJECT"
  run zsh "$SCRIPT" branch-name

  [ "$status" -eq 1 ]
  [[ "$output" == *"usage:"* ]]
}

@test "fails when branch does not exist on upstream" {
  cd "$PROJECT"
  run zsh "$SCRIPT" nonexistent-branch "$BATS_TEST_TMPDIR/worktree"

  [ "$status" -eq 2 ]
  [[ "$output" == *"does not exist"* ]]
}

@test "fails when target path already exists" {
  cd "$PROJECT"
  local target="$BATS_TEST_TMPDIR/existing_path"
  touch "$target"

  run zsh "$SCRIPT" feature-branch "$target"

  [ "$status" -eq 3 ]
  [[ "$output" == *"already exists"* ]]
}

@test "creates worktree for valid branch" {
  cd "$PROJECT"
  git fetch upstream
  local target="$BATS_TEST_TMPDIR/worktree"

  run zsh "$SCRIPT" feature-branch "$target"

  [ "$status" -eq 0 ]
  [ -d "$target" ]
}
