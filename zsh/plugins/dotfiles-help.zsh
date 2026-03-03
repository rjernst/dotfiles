# Display all custom dotfiles commands/aliases, grouped by category.
# Commands are discovered via # @help annotations in source files.
# Run via: dotfiles-help

_help_scan_file() {
  local file=$1 category=$2
  [[ -f "$file" ]] || return
  local line
  while IFS= read -r line; do
    if [[ "$line" =~ '#[[:space:]]*@help[[:space:]]+(.+)[[:space:]]+--[[:space:]]+(.+)' ]]; then
      local cmd="${match[1]}"
      local desc="${match[2]}"
      _help_entries+=("${category}	${cmd}	${desc}")
    fi
  done < "$file"
}

_help_category_name() {
  local name=$1
  # Special case mappings
  case $name in
    elasticsearch-support) echo "ES Support" ;;
    *)
      # Strip dotfiles- prefix, capitalize first letter of each word
      name=${name#dotfiles-}
      echo "${(C)name//-/ }"
      ;;
  esac
}

_dotfiles_help() {
  local -a _help_entries

  # 1. Core: zshrc
  _help_scan_file "$DOTFILES/zsh/zshrc" "Core"

  # 2. Shared plugins
  for plugin in "$DOTFILES"/zsh/plugins/*.zsh(N); do
    local name=${plugin:t:r}
    local category=$(_help_category_name "$name")
    _help_scan_file "$plugin" "$category"
  done

  # 3. Active role plugins
  local roles_file="$DOTFILES/hosts/$(hostname)/roles"
  if [[ -f "$roles_file" ]]; then
    local role
    while IFS= read -r role; do
      [[ -z "$role" || "$role" = \#* ]] && continue
      local plugin_file="$DOTFILES/roles/$role/zsh_plugin"
      if [[ -f "$plugin_file" ]]; then
        local category=$(_help_category_name "$role")
        _help_scan_file "$plugin_file" "$category"
      fi
    done < "$roles_file"
  fi

  # 4. Git aliases
  _help_scan_file "$DOTFILES/git/config" "Git Aliases"

  # Collect unique categories in order (Core first, then alphabetical, Git Aliases last)
  local -a categories
  local -A seen
  for entry in "${_help_entries[@]}"; do
    local cat="${entry%%	*}"
    if [[ -z "${seen[$cat]}" ]]; then
      seen[$cat]=1
      categories+=("$cat")
    fi
  done

  # Sort: Core first, Git Aliases last, rest alphabetical
  local -a sorted_categories
  local -a middle
  for cat in "${categories[@]}"; do
    case $cat in
      Core) ;;
      "Git Aliases") ;;
      *) middle+=("$cat") ;;
    esac
  done
  middle=(${(o)middle})

  [[ -n "${seen[Core]}" ]] && sorted_categories+=("Core")
  sorted_categories+=("${middle[@]}")
  [[ -n "${seen[Git Aliases]}" ]] && sorted_categories+=("Git Aliases")

  # Print
  for cat in "${sorted_categories[@]}"; do
    print -P "\n%F{cyan}%B${cat}%b%f"
    for entry in "${_help_entries[@]}"; do
      local entry_cat="${entry%%	*}"
      [[ "$entry_cat" != "$cat" ]] && continue
      local rest="${entry#*	}"
      local cmd="${rest%%	*}"
      local desc="${rest#*	}"
      printf "  %-22s %s\n" "$cmd" "$desc"
    done
  done
}

# @help help -- Show all custom commands and aliases
alias help='_dotfiles_help'
alias dotfiles-help='_dotfiles_help'
