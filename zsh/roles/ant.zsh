
function _set_ant_home() {
  local ant_bin=$(which ant)
  local real_ant_bin=$ant_bin:A
  local ant_libexec=$(dirname $real_ant_bin)/../libexec
  export ANT_HOME=$ant_libexec:A
}
if command -v ant &> /dev/null; then
  _set_ant_home
fi
# vi: set tabstop=2 shiftwidth=2 filetype=zsh expandtab:
