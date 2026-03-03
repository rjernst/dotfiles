#!/usr/bin/env zsh
# Helper: sources the java role's zsh_plugin and runs _find_latest_jdk.
# Expects: DOTFILES, MOCK_BIN, JENV_DATA_FILE set by caller.
# Prints: LATEST=<val> and VERSION=<val> lines for BATS assertions.

set -e

export PATH="$MOCK_BIN:$PATH"

# Provide the helper that zsh_plugin depends on (normally defined in zshrc)
_dotfiles_require_cmds() {
  for cmd in "${@:2}"; do
    (( $+commands[$cmd] )) || return 1
  done
}

source "$DOTFILES/roles/java/zsh_plugin"

_find_latest_jdk

echo "LATEST=$_latest_jdk_version"
for v in $_jdk_major_versions; do
  echo "VERSION=$v"
done
