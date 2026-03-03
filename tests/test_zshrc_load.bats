#!/usr/bin/env bats

bats_require_minimum_version 1.5.0

# Test that zshrc loads without errors in a sandboxed environment.
# Uses a fake HOME with a zinit stub and symlinked plugins so we exercise
# the real zshrc without needing network access or installed tools.

DOTFILES_ROOT="${BATS_TEST_FILENAME%/tests/*}"

setup() {
  export HOME="$BATS_TEST_TMPDIR/home"
  mkdir -p "$HOME"

  # Symlink zshrc
  mkdir -p "$HOME/.zsh/var" "$HOME/.zsh/plugins"
  ln -s "$DOTFILES_ROOT/zsh/zshrc" "$HOME/.zshrc"

  # Create a fake DOTFILES location where zshrc expects it
  ln -s "$DOTFILES_ROOT" "$HOME/.dotfiles"

  # Stub zinit: create the file zshrc sources, providing a no-op zinit function
  # and stubs for the autoload/compdef helpers it expects
  mkdir -p "$HOME/.zsh/zinit/bin"
  cat > "$HOME/.zsh/zinit/bin/zinit.zsh" << 'STUB'
zinit() { : }
zicompinit() { : }
zicdreplay() { : }
STUB

  # Symlink shared plugins
  for plugin in "$DOTFILES_ROOT"/zsh/plugins/*.zsh; do
    ln -s "$plugin" "$HOME/.zsh/plugins/$(basename "$plugin")"
  done

  # Symlink role plugins (as setup script would)
  for role_plugin in "$DOTFILES_ROOT"/roles/*/zsh_plugin; do
    local role_name
    role_name=$(basename "$(dirname "$role_plugin")")
    ln -s "$role_plugin" "$HOME/.zsh/plugins/${role_name}.zsh"
  done
}

@test "zshrc loads without errors" {
  run zsh -i -c 'echo ok' 2>&1

  [ "$status" -eq 0 ]
  # The last line of output should be 'ok' (preceding lines may be
  # warnings from _dotfiles_require_cmds about missing tools, which is fine)
  local last_line="${lines[$(( ${#lines[@]} - 1 ))]}"
  [[ "$last_line" == "ok" ]]
}
