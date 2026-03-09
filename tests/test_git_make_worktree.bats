#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/git-make-worktree (deprecated wrapper for ta wt create)
# Uses temp git repos to simulate a project with an upstream remote.

setup() {
  SCRIPT="${BATS_TEST_FILENAME%/*}/../scripts/git-make-worktree"

  export PROJECT="$BATS_TEST_TMPDIR/project"
  export GIT_AUTHOR_NAME="Test"
  export GIT_AUTHOR_EMAIL="test@test.com"
  export GIT_COMMITTER_NAME="Test"
  export GIT_COMMITTER_EMAIL="test@test.com"
  export GIT_CONFIG_GLOBAL="$BATS_TEST_TMPDIR/gitconfig"
  echo "[commit]
  gpgsign = false" > "$GIT_CONFIG_GLOBAL"

  # Create an upstream bare repo
  git init --bare "$BATS_TEST_TMPDIR/upstream.git"

  # Create project repo with an upstream remote and push a branch
  git init "$PROJECT"
  git -C "$PROJECT" commit --allow-empty -m "initial"
  git -C "$PROJECT" remote add upstream "$BATS_TEST_TMPDIR/upstream.git"
  git -C "$PROJECT" push upstream HEAD:main
  git -C "$PROJECT" push upstream HEAD:feature-branch
}

@test "prints deprecation warning" {
  cd "$PROJECT"
  run zsh "$SCRIPT" feature-branch "$BATS_TEST_TMPDIR/worktree"

  [[ "$output" == *"deprecated"* ]]
}

@test "fails with no arguments" {
  cd "$PROJECT"
  run zsh "$SCRIPT"

  [ "$status" -ne 0 ]
  [[ "$output" == *"usage:"* ]] || [[ "$output" == *"deprecated"* ]]
}

@test "fails when branch does not exist on upstream" {
  cd "$PROJECT"
  run zsh "$SCRIPT" nonexistent-branch "$BATS_TEST_TMPDIR/worktree"

  [ "$status" -ne 0 ]
}

@test "creates worktree for valid branch" {
  cd "$PROJECT"
  git fetch upstream
  local target="$BATS_TEST_TMPDIR/worktree"

  run zsh "$SCRIPT" feature-branch "$target"

  [ "$status" -eq 0 ]
  [ -d "$target" ]
}
