#!/usr/bin/env zsh
# Helper: extracts _set_env and runs it with given arguments.
# Expects: DOTFILES, MOCK_BIN set by caller.
# Prints: RC=<val> and env var values.

export PATH="$MOCK_BIN:$PATH"

eval "$(sed -n '/^function _set_env()/,/^}/p' "$DOTFILES/roles/elasticsearch-support/zsh_plugin")"

_set_env "$@"

echo "RC=$?"
echo "ENV_NAME=${ENV_NAME:-UNSET}"
echo "TSH_PROXY=${TSH_PROXY:-UNSET}"
echo "ENV_URL=${ENV_URL:-UNSET}"
echo "ADMIN_UI=${ADMIN_UI:-UNSET}"
echo "API_KEY=${API_KEY:-UNSET}"
