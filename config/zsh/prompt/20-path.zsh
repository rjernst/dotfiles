_path_info() {
  local max_len=$1
  local path=${(%):-%~}
  local path_len=${#path}
  if [ $path_len -gt $max_len ]; then
    path="%$(( $1 ))<...<%~%<<"
  fi
  echo -n "$(_lc green $path)"
}

left_info_functions+=(_path_info)
