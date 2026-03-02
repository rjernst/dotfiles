#!/usr/bin/env zsh
# Helper: extracts _find_pipenv from pipenv.zsh and runs it.
# Usage: zsh find_pipenv.zsh <directory>
# Expects: DOTFILES set by caller.
# Prints: PIPENV_ROOT=<val> or PIPENV_ROOT=UNSET

set -e

eval "$(sed -n '/^_find_pipenv()/,/^}/p' "$DOTFILES/zsh/plugins/pipenv.zsh")"

cd "$1"
_find_pipenv

if [ -n "$PIPENV_ROOT" ]; then
  echo "PIPENV_ROOT=$PIPENV_ROOT"
else
  echo "PIPENV_ROOT=UNSET"
fi
