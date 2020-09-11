autoload colors zsh/terminfo
if [[ "$terminfo[colors]" -ge 8 ]]; then
  colors
fi

# color chart
# https://upload.wikimedia.org/wikipedia/commons/1/15/Xterm_256color_chart.svg

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
