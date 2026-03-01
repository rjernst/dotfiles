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
