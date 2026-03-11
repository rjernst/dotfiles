#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for scripts/workspace-switcher (syntax and structure checks)
# Full integration requires tmux + fzf, so we focus on syntax validation.

setup() {
  SCRIPT="${BATS_TEST_FILENAME%/*}/../scripts/workspace-switcher"
}

@test "workspace-switcher passes zsh syntax check" {
  run zsh -n "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher uses 50% width default" {
  run grep 'width=.*50%' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher uses 40% height default" {
  run grep 'height=.*40%' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher captures ORIG_PANE_PATH" {
  run grep 'ORIG_PANE_PATH' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher exports CURRENT_SESSION" {
  run grep 'export CURRENT_SESSION' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher uses workspace-switcher-list for fzf input" {
  run grep 'workspace-switcher-list' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher has tab binding for collapse toggle" {
  run grep 'tab:execute-silent' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher has ctrl-d binding for kill" {
  run grep 'ctrl-d:execute-silent' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher has ctrl-a binding" {
  run grep 'ctrl-a:' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher has two-line header with keybinding hints" {
  run grep 'enter switch' "$SCRIPT"
  [ "$status" -eq 0 ]
  run grep '● current' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher uses --ansi flag" {
  run grep '\-\-ansi' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher handles GROUP: header on Enter" {
  run grep 'GROUP:\*' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher ctrl-a uses become to output AGENT_LOOP marker" {
  run grep 'ctrl-a:become(echo AGENT_LOOP)' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher handles AGENT_LOOP target" {
  run grep 'target.*AGENT_LOOP' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher detects git root from ORIG_PANE_PATH" {
  run grep 'git -C.*ORIG_PANE_PATH.*rev-parse --show-toplevel' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher creates al- prefixed session" {
  run grep 'al-\$project' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher sends ralph command to new agent-loop session" {
  run grep 'ralph --timeout 4h' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher checks if ralph is available before sending" {
  run grep 'command -v ralph' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher shows error when not in git repo" {
  run grep 'not in a git repository' "$SCRIPT"
  [ "$status" -eq 0 ]
}
