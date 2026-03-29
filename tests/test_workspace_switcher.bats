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

@test "workspace-switcher calculates popup height from content" {
  run grep 'workspace-switcher-list.*wc -l' "$SCRIPT"
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

@test "workspace-switcher exports CURRENT_WINDOW" {
  run grep 'export CURRENT_WINDOW' "$SCRIPT"
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

@test "workspace-switcher ctrl-d handles window targets with kill-window" {
  run grep 'kill-window' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher ctrl-d handles session targets with kill-session" {
  run grep 'kill-session' "$SCRIPT"
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

@test "workspace-switcher delegates agent loop creation to ta" {
  run grep 'ta agent-loop start' "$SCRIPT"
  [ "$status" -eq 0 ]
}

@test "workspace-switcher passes ORIG_PANE_PATH to ta agent-loop" {
  run grep 'ORIG_PANE_PATH' "$SCRIPT"
  [ "$status" -eq 0 ]
}
