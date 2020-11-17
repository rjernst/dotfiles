
# install jenv
export PATH="$HOME/.jenv/bin:$PATH"
_init_jenv() {
  eval "$(jenv init -)"
}
_init_jenv

# shortcut to current java home
alias jdk='readlink $(jenv javahome)'

# create a shortcut for setting to each major version
function _setup_jdk_aliases() {
  _jdk_major_versions=("${(@f)$(jenv versions | grep -E "((\d+\.0)|(1.8))$" | sed -E 's/.* ([0-9]+)\.0$/\1/' | sed -E 's/.* (1.8)$/\1/' | sort -r --unique)}")
  for v in $_jdk_major_versions; do
    major=$v
    if [ "$v" = '1.8' ]; then
      major=8
    fi
    alias jdk${major}='jenv shell '${v}
    export JAVA${major}_HOME=$HOME/.jenv/versions/${v}
  done
}
_setup_jdk_aliases

function _reload_jenv() {
  if [[ $OSTYPE == darwin* ]]; then
    local jdk_base=/Library/Java/JavaVirtualMachines
    local jdk_suffix="Contents/Home"
  fi

  rm -rf $HOME/.jenv
  _init_jenv
  for jdk in $jdk_base/*; do
    echo "Adding jdk at $jdk"
    jenv add $jdk/$jdk_suffix
  done

  # set the global to be the newest. note that this won't be an ea release because that
  # is excluded above in the major version  
  jenv global ${_jdk_major_versions[1]}

  _setup_jdk_aliases
}
alias reload-jenv='_reload_jenv'

# vi: set tabstop=2 shiftwidth=2 filetype=zsh expandtab:
