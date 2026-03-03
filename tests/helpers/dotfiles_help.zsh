#!/usr/bin/env zsh
# Helper: sources the help plugin and runs _dotfiles_help.
# Expects: HOME, DOTFILES, ZDOTDIR set by caller.
# Prints: the full help output.

set -e

# Provide hostname if not available
(( $+commands[hostname] )) || hostname() { echo "testhost" }

source "$DOTFILES/zsh/plugins/dotfiles-help.zsh"

_dotfiles_help
