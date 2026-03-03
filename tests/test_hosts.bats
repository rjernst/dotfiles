#!/usr/bin/env bats

# Validates that host/role references are consistent:
# every role listed in a host's roles file must have a matching roles/<name>/ directory.

setup() {
  DOTFILES_DIR="${BATS_TEST_FILENAME%/*}/.."
}

@test "all roles referenced by hosts exist" {
  local missing=()
  for roles_file in "$DOTFILES_DIR"/hosts/*/roles; do
    local host
    host=$(basename "$(dirname "$roles_file")")
    while IFS= read -r role; do
      [ -z "$role" ] && continue
      if [ ! -d "$DOTFILES_DIR/roles/$role" ]; then
        missing+=("$host references missing role: $role")
      fi
    done < "$roles_file"
  done

  if [ ${#missing[@]} -gt 0 ]; then
    printf '%s\n' "${missing[@]}"
    return 1
  fi
}

@test "no empty roles files" {
  for roles_file in "$DOTFILES_DIR"/hosts/*/roles; do
    local host
    host=$(basename "$(dirname "$roles_file")")
    local count
    count=$(grep -c '[^[:space:]]' "$roles_file" || true)
    if [ "$count" -eq 0 ]; then
      echo "$host has empty roles file"
      return 1
    fi
  done
}

@test "all roles in requires files exist" {
  local missing=()
  for req_file in "$DOTFILES_DIR"/roles/*/requires; do
    [ -f "$req_file" ] || continue
    local role
    role=$(basename "$(dirname "$req_file")")
    while IFS= read -r dep; do
      [ -z "$dep" ] && continue
      [[ "$dep" = \#* ]] && continue
      if [ ! -d "$DOTFILES_DIR/roles/$dep" ]; then
        missing+=("role '$role' requires missing role: $dep")
      fi
    done < "$req_file"
  done

  if [ ${#missing[@]} -gt 0 ]; then
    printf '%s\n' "${missing[@]}"
    return 1
  fi
}

@test "no cycles in role dependency graph" {
  # DFS cycle detection in bash using string-based sets
  local resolved=""
  local in_stack=""

  _check_cycle() {
    local role=$1
    # Already fully processed
    [[ " $resolved " == *" $role "* ]] && return 0
    # Cycle detected
    if [[ " $in_stack " == *" $role "* ]]; then
      echo "cycle detected involving role: $role"
      return 1
    fi
    in_stack="$in_stack $role"
    local req_file="$DOTFILES_DIR/roles/$role/requires"
    if [ -f "$req_file" ]; then
      while IFS= read -r dep; do
        [ -z "$dep" ] && continue
        [[ "$dep" = \#* ]] && continue
        _check_cycle "$dep" || return 1
      done < "$req_file"
    fi
    # Remove from in_stack, add to resolved
    in_stack="${in_stack/ $role/}"
    resolved="$resolved $role"
  }

  for role_dir in "$DOTFILES_DIR"/roles/*/; do
    [ -d "$role_dir" ] || continue
    local role
    role=$(basename "$role_dir")
    _check_cycle "$role" || return 1
  done
}
