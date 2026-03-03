#!/usr/bin/env zsh
# Helper for dependency resolution tests: resolves role dependencies and prints
# the resulting order one role per line.
# Usage: zsh resolve_roles.zsh <role1> [role2 ...]
# Expects DOTFILES to be set by the caller.

set -e

typeset -A _resolved _in_stack
_order=()

resolve_role() {
  local role=$1
  (( ${+_resolved[$role]} )) && return 0
  if (( ${+_in_stack[$role]} )); then
    echo "ERROR: dependency cycle detected involving role '$role'" >&2
    return 1
  fi
  _in_stack[$role]=1
  if [ ! -d "$DOTFILES/roles/$role" ]; then
    echo "ERROR: required role '$role' not found" >&2
    return 1
  fi
  local requires_file="$DOTFILES/roles/$role/requires"
  if [ -f "$requires_file" ]; then
    local dep
    while IFS= read -r dep; do
      [[ -z "$dep" || "$dep" = \#* ]] && continue
      resolve_role "$dep" || return 1
    done < "$requires_file"
  fi
  unset "_in_stack[$role]"
  _resolved[$role]=1
  _order+=("$role")
}

for role in "$@"; do
  resolve_role "$role" || exit 1
done

for role in "${_order[@]}"; do
  echo "$role"
done
