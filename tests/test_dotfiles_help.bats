#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Tests for dotfiles-help plugin annotation scanning and output.

HELPER="${BATS_TEST_FILENAME%/*}/helpers/dotfiles_help.zsh"

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  export DOTFILES="$BATS_TEST_TMPDIR/dotfiles"
  export ZDOTDIR="$HOME/.zsh"
  mkdir -p "$HOME" "$DOTFILES/zsh/plugins" "$DOTFILES/git"

  # Copy the help plugin into the fake dotfiles
  cp "$(cd "${BATS_TEST_FILENAME%/*}/.." && pwd)/zsh/plugins/dotfiles-help.zsh" \
     "$DOTFILES/zsh/plugins/dotfiles-help.zsh"
}

@test "help: parses @help annotations from zshrc" {
  cat > "$DOTFILES/zsh/zshrc" <<'EOF'
# @help reload-config -- Re-source ~/.zshrc
alias reload-config='source ~/.zshrc'
# @help cdd -- cd to dotfiles directory
alias cdd='cd $DOTFILES'
EOF

  run zsh -c "
    hostname() { echo nohost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"Core"* ]]
  [[ "$output" == *"reload-config"* ]]
  [[ "$output" == *"Re-source ~/.zshrc"* ]]
  [[ "$output" == *"cdd"* ]]
}

@test "help: groups by category with correct headers" {
  cat > "$DOTFILES/zsh/zshrc" <<'EOF'
# @help h -- Show history
alias h='history'
EOF
  cat > "$DOTFILES/zsh/plugins/test-plugin.zsh" <<'EOF'
# @help test-cmd -- A test command
alias test-cmd='echo test'
EOF

  run zsh -c "
    hostname() { echo nohost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"Core"* ]]
  [[ "$output" == *"Test Plugin"* ]]
  [[ "$output" == *"test-cmd"* ]]
}

@test "help: files with no annotations produce no empty categories" {
  cat > "$DOTFILES/zsh/zshrc" <<'EOF'
alias h='history'
EOF
  cat > "$DOTFILES/zsh/plugins/empty.zsh" <<'EOF'
# No help annotations here
alias something='echo hi'
EOF

  run zsh -c "
    hostname() { echo nohost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  # Should not have any category headers since no annotations
  [[ "$output" != *"Core"* ]]
  [[ "$output" != *"Empty"* ]]
}

@test "help: git config annotations appear under Git Aliases" {
  touch "$DOTFILES/zsh/zshrc"
  cat > "$DOTFILES/git/config" <<'EOF'
[alias]
  # @help git s -- Show status
  s = status
  # @help git co -- Checkout a branch
  co = checkout
EOF

  run zsh -c "
    hostname() { echo nohost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"Git Aliases"* ]]
  [[ "$output" == *"git s"* ]]
  [[ "$output" == *"Show status"* ]]
  [[ "$output" == *"git co"* ]]
}

@test "help: role plugins scanned for active roles" {
  touch "$DOTFILES/zsh/zshrc"
  mkdir -p "$DOTFILES/hosts/testhost" "$DOTFILES/roles/myrole"
  echo "myrole" > "$DOTFILES/hosts/testhost/roles"
  cat > "$DOTFILES/roles/myrole/zsh_plugin" <<'EOF'
# @help myrole-cmd -- Do something role-specific
alias myrole-cmd='echo role'
EOF

  run zsh -c "
    hostname() { echo testhost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"Myrole"* ]]
  [[ "$output" == *"myrole-cmd"* ]]
  [[ "$output" == *"Do something role-specific"* ]]
}

@test "help: elasticsearch-support category name" {
  touch "$DOTFILES/zsh/zshrc"
  mkdir -p "$DOTFILES/hosts/testhost" "$DOTFILES/roles/elasticsearch-support"
  echo "elasticsearch-support" > "$DOTFILES/hosts/testhost/roles"
  cat > "$DOTFILES/roles/elasticsearch-support/zsh_plugin" <<'EOF'
# @help set-env -- Log into support env
alias set-env='_set_env'
EOF

  run zsh -c "
    hostname() { echo testhost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"ES Support"* ]]
  [[ "$output" == *"set-env"* ]]
}

@test "help: Core appears before other categories" {
  cat > "$DOTFILES/zsh/zshrc" <<'EOF'
# @help h -- Show history
alias h='history'
EOF
  cat > "$DOTFILES/git/config" <<'EOF'
  # @help git s -- Show status
EOF

  run zsh -c "
    hostname() { echo nohost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  # Core should appear before Git Aliases in the output
  local core_pos git_pos
  core_pos=$(echo "$output" | grep -n "Core" | head -1 | cut -d: -f1)
  git_pos=$(echo "$output" | grep -n "Git Aliases" | head -1 | cut -d: -f1)
  [ "$core_pos" -lt "$git_pos" ]
}

@test "help: dotfiles- prefix stripped from plugin category" {
  touch "$DOTFILES/zsh/zshrc"
  cat > "$DOTFILES/zsh/plugins/dotfiles-update-check.zsh" <<'EOF'
# @help check-updates -- Check for dotfiles updates
EOF

  run zsh -c "
    hostname() { echo nohost }
    export HOME='$HOME' DOTFILES='$DOTFILES' ZDOTDIR='$ZDOTDIR'
    source '$DOTFILES/zsh/plugins/dotfiles-help.zsh'
    _dotfiles_help
  "

  [ "$status" -eq 0 ]
  [[ "$output" == *"Update Check"* ]]
  [[ "$output" != *"Dotfiles Update Check"* ]]
}
