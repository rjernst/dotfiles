#!/usr/bin/env zsh
# Helper: sources the java role's zsh_plugin and runs _find_latest_jdk.
# Expects: DOTFILES, MOCK_BIN, JENV_DATA_FILE set by caller.
# Prints: LATEST=<val> and VERSION=<val> lines for BATS assertions.

set -e

export PATH="$MOCK_BIN:$PATH"
source "$DOTFILES/roles/java/zsh_plugin"

_find_latest_jdk

echo "LATEST=$_latest_jdk_version"
for v in $_jdk_major_versions; do
  echo "VERSION=$v"
done
