#!/usr/bin/env zsh
# Helper: extracts _brew_drift from zshrc and runs it.
# Expects: DOTFILES, MOCK_BIN set by caller.
# The function is inside the darwin* block, so we extract it with awk.

set -e

export PATH="$MOCK_BIN:$PATH"

# Extract the function from the darwin block in zshrc (2-space indented)
eval "$(awk '
  /^  function _brew_drift\(\)/ { found=1 }
  found { print; if (/^  \}$/) exit }
' "$DOTFILES/zsh/zshrc" | sed 's/^  //')"

_brew_drift "$@"
