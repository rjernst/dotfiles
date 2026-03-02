#!/usr/bin/env zsh
# Helper: extracts _dotfiles_check_updates and runs it.
# Expects: ZDOTDIR, DOTFILES set by caller.
# Prints: function output, then CACHE_EXISTS and STAMP diagnostic lines.

eval "$(sed -n '/^_dotfiles_check_updates()/,/^}/p' "$DOTFILES/zsh/plugins/dotfiles-update-check.zsh")"

_dotfiles_check_updates

if [[ -f "$ZDOTDIR/var/dotfiles-behind-count" ]]; then
  echo "CACHE_EXISTS=true"
  echo "CACHE_VALUE=$(<"$ZDOTDIR/var/dotfiles-behind-count")"
else
  echo "CACHE_EXISTS=false"
fi

if [[ -f "$ZDOTDIR/var/dotfiles-fetch-stamp" ]]; then
  echo "STAMP_EXISTS=true"
  echo "STAMP_VALUE=$(<"$ZDOTDIR/var/dotfiles-fetch-stamp")"
else
  echo "STAMP_EXISTS=false"
fi
