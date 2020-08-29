autoload colors zsh/terminfo
if [[ "$terminfo[colors]" -ge 8 ]]; then
  colors
fi

typeset -A altchar
set -A altchar ${(s..)terminfo[acsc]}

declare -A sc
sc[set_charset]="%{$terminfo[enacs]%}"
sc[shift_in]="%{$terminfo[smacs]%}"
sc[shift_out]="%{$terminfo[rmacs]%}"
sc[hbar]=${altchar[q]:--}
sc[ul]=${altchar[l]:--}
sc[ll]=${altchar[m]:--}
sc[lr]=${altchar[j]:--}
sc[ur]=${altchar[k]:--}

_special() {
  echo -n "${sc[shift_in]}"
  for arg in "${@}"; do
    echo -n "${sc[$arg]}"
  done
  echo -n "${sc[shift_out]}"
}

# bold color
_bc() {
  echo -n "%B%F{$1}$2%f%b"
}

# light color
_lc() {
  echo -n "%F{$1}$2%f"
}

# no color
_nc() {
  echo -n "%{$terminfo[sgr0]%}$1"
}

_debug() {
  if [ $PROMPT_DEBUG ]; then
    echo "prompt: $1" >&2
  fi
}

prompt-debug() {
  PROMPT_DEBUG=1
}

# --------------- filled in by subsequent scripts ----------------
left_info_functions=()
right_info_functions=()
chdir_functions=()
notify_functions=()
