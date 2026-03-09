#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/ta-ff (fork and focus)
# Uses real git repos for branch/worktree operations and mock tmux/workspace.

setup() {
  TA="${BATS_TEST_FILENAME%/*}/../scripts/ta"
  TA_FF="${BATS_TEST_FILENAME%/*}/../scripts/ta-ff"

  export GIT_AUTHOR_NAME="Test"
  export GIT_AUTHOR_EMAIL="test@test.com"
  export GIT_COMMITTER_NAME="Test"
  export GIT_COMMITTER_EMAIL="test@test.com"

  # Disable commit signing for tests
  export GIT_CONFIG_GLOBAL="$BATS_TEST_TMPDIR/gitconfig"
  git config --global commit.gpgsign false
  git config --global tag.gpgsign false
  git config --global init.defaultBranch main

  # Create a bare "upstream" repo
  git init --bare "$BATS_TEST_TMPDIR/upstream.git"

  # Create the project repo
  export PROJECT="$BATS_TEST_TMPDIR/project"
  git init "$PROJECT"
  git -C "$PROJECT" commit --allow-empty -m "initial commit"
  git -C "$PROJECT" branch -M main
  git -C "$PROJECT" remote add upstream "$BATS_TEST_TMPDIR/upstream.git"
  git -C "$PROJECT" push upstream main

  # Create mock directory
  MOCK_DIR="$BATS_TEST_TMPDIR/mock-bin"
  mkdir -p "$MOCK_DIR"

  # Mock workspace script (tracks calls)
  WORKSPACE_LOG="$BATS_TEST_TMPDIR/workspace-log"
  : > "$WORKSPACE_LOG"
  export TA_WORKSPACE_CMD="$MOCK_DIR/ta-workspace"
  cat > "$TA_WORKSPACE_CMD" <<SCRIPT
#!/usr/bin/env bash
echo "\$@" >> "$WORKSPACE_LOG"
exit 0
SCRIPT
  chmod +x "$TA_WORKSPACE_CMD"

  # Use real ta-wt script
  export TA_WT_CMD="${BATS_TEST_FILENAME%/*}/../scripts/ta-wt"

  # Unset TMUX
  unset TMUX
}

# --- dispatcher tests ---

@test "ta ff dispatches to ta-ff" {
  run zsh "$TA" ff
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "ta-ff with no args shows usage" {
  run zsh "$TA_FF"
  [ "$status" -eq 2 ]
  [[ "$output" == *"usage:"* ]]
}

@test "ta-ff unknown option fails" {
  cd "$PROJECT"
  run zsh "$TA_FF" --bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option"* ]]
}

# --- full flow tests ---

@test "ff: creates branch, worktree, and workspace" {
  cd "$PROJECT"

  run zsh "$TA_FF" fix/test-thing
  [ "$status" -eq 0 ]

  # Branch was created
  git branch --list fix/test-thing | grep -q "fix/test-thing"

  # Worktree was created
  local expected_path="${PROJECT%/*}/project-fix-test-thing"
  [ -d "$expected_path" ]

  # Branch was pushed to upstream
  git ls-remote --heads upstream refs/heads/fix/test-thing | grep -q "fix/test-thing"

  # Workspace create and attach were called
  grep -q "create fix/test-thing" "$WORKSPACE_LOG"
  grep -q "attach fix/test-thing" "$WORKSPACE_LOG"
}

@test "ff: existing worktree skips creation and attaches" {
  cd "$PROJECT"

  # Create an existing worktree manually
  git checkout -b existing-branch
  git commit --allow-empty -m "existing branch work"
  git checkout main
  local wt_path="$BATS_TEST_TMPDIR/wt-existing"
  git worktree add "$wt_path" existing-branch

  run zsh "$TA_FF" existing-branch
  [ "$status" -eq 0 ]
  [[ "$output" == *"already has a worktree"* ]]

  # Workspace create and attach were called
  grep -q "create existing-branch" "$WORKSPACE_LOG"
  grep -q "attach existing-branch" "$WORKSPACE_LOG"
}

@test "ff: --from-worktree skips branch creation" {
  cd "$PROJECT"

  # Create worktree first
  git checkout -b from-wt-branch
  git commit --allow-empty -m "from-wt work"
  git checkout main
  local wt_path="$BATS_TEST_TMPDIR/wt-from-wt"
  git worktree add "$wt_path" from-wt-branch

  run zsh "$TA_FF" --from-worktree from-wt-branch
  [ "$status" -eq 0 ]

  # Workspace create and attach were called
  grep -q "create from-wt-branch" "$WORKSPACE_LOG"
  grep -q "attach from-wt-branch" "$WORKSPACE_LOG"
}

@test "ff: --from-worktree fails if no worktree exists" {
  cd "$PROJECT"

  run zsh "$TA_FF" --from-worktree nonexistent-branch
  [ "$status" -eq 1 ]
  [[ "$output" == *"no worktree found"* ]]
}

@test "ff: prompt string is passed as --cmd" {
  cd "$PROJECT"

  # Create an existing worktree to skip git operations
  git checkout -b prompt-branch
  git commit --allow-empty -m "prompt branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-prompt" prompt-branch

  run zsh "$TA_FF" prompt-branch "Fix the NPE in SearchService.java"
  [ "$status" -eq 0 ]

  # Workspace create was called with --cmd containing the prompt
  grep -q "create prompt-branch --cmd" "$WORKSPACE_LOG"
}

@test "ff: --cmd is passed to workspace create" {
  cd "$PROJECT"

  # Create an existing worktree
  git checkout -b cmd-branch
  git commit --allow-empty -m "cmd branch"
  git checkout main
  git worktree add "$BATS_TEST_TMPDIR/wt-cmd" cmd-branch

  run zsh "$TA_FF" cmd-branch --cmd "git status"
  [ "$status" -eq 0 ]

  # Workspace create was called with --cmd
  grep -q "create cmd-branch --cmd git status" "$WORKSPACE_LOG"
}

@test "ff: uses upstream remote by default" {
  cd "$PROJECT"

  run zsh "$TA_FF" fix/upstream-test
  [ "$status" -eq 0 ]
  [[ "$output" == *"pushing"*"upstream"* ]]

  # Verify pushed to upstream
  git ls-remote --heads upstream refs/heads/fix/upstream-test | grep -q "fix/upstream-test"
}

@test "ff: falls back to origin when no upstream" {
  cd "$PROJECT"
  git remote remove upstream
  git remote add origin "$BATS_TEST_TMPDIR/upstream.git"

  run zsh "$TA_FF" fix/origin-test
  [ "$status" -eq 0 ]
  [[ "$output" == *"pushing"*"origin"* ]]
}

@test "ff: --remote overrides default" {
  cd "$PROJECT"
  # Add origin as a second remote pointing to same bare repo
  git remote add origin "$BATS_TEST_TMPDIR/upstream.git"

  run zsh "$TA_FF" fix/remote-override --remote origin
  [ "$status" -eq 0 ]
  [[ "$output" == *"pushing"*"origin"* ]]
}

@test "ff: fails if no remote available" {
  cd "$PROJECT"
  git remote remove upstream

  run zsh "$TA_FF" fix/no-remote
  [ "$status" -eq 1 ]
  [[ "$output" == *"no remote found"* ]]
}

@test "ff: fails if worktree path already exists" {
  cd "$PROJECT"
  local expected_path="${PROJECT%/*}/project-fix-path-exists"
  mkdir -p "$expected_path"

  run zsh "$TA_FF" fix/path-exists
  [ "$status" -eq 1 ]
  [[ "$output" == *"path already exists"* ]]
}
