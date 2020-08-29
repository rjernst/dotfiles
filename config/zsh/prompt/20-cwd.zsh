_cwd_info() {
  local max_len=$1
  local cwd=${(%):-%~}
  _debug "cwd: $cwd"
  local cwd_len=${#cwd}
  _debug "cwd len: $cwd_len"
  if [ $cwd_len -gt $max_len ]; then
    cwd="%$(( $1 ))<...<%~%<<"
    cwd_len=$1
  fi
  echo -n "$cwd_len:$(_lc green $cwd)"
}

left_info_functions+=(_cwd_info)
