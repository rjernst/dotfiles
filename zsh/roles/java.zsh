
# install jenv
export PATH="$HOME/.jenv/bin:$PATH"
_init_jenv() {
  eval "$(jenv init -)"
}
_init_jenv

# shortcut to current java home
alias jdk='readlink $(jenv javahome)'

 #_jdk_major_versions=("${(@f)$(jenv versions | grep -E "^\s+((\d+(-ea)?)|(1.8))$" | sed -E 's/.* ([0-9]+)\.0$/\1/' | sed -E 's/.* (1.8)$/\1/' | sort -r --unique)}")
# create a shortcut for setting to each major version
function _setup_jdk_aliases() {
  _jdk_major_versions=("${(@f)$(jenv versions | grep -E "^\s+((\d+(-ea)?)|(1.8))$" | sed -E 's/^ *//g' | sort -r --unique)}")
  _latest_jdk_version=
  for v in $_jdk_major_versions; do
    major=$v
    if [ "$v" = '1.8' ]; then
      major=8
    fi
    if [[ $v == *-ea ]]; then
      major=${v:0:(-3)}
    elif [[ -z "$_latest_jdk_version" ]]; then
      _latest_jdk_version=$major
    fi
    alias jdk${major}='jenv shell '${v}
    export JAVA${major}_HOME=$HOME/.jenv/versions/${v}
  done
}
_setup_jdk_aliases

function _reload_jenv() {
  rm -rf $HOME/.jenv
  _init_jenv

  if [[ $OSTYPE == darwin* ]]; then
    # system jdks
    for jdk in /Library/Java/JavaVirtualMachines/*; do
      echo "Adding system jdk at $jdk"
      jenv add $jdk/Contents/Home
    done

    # user jdks
    if [ -d $HOME/Library/Java/JavaVirtualMachines]; then
      for jdk in $HOME/Library/Java/JavaVirtualMachines/*; do
        echo "Adding user jdk at $jdk"
        jenv add $jdk/Contents/Home
      done
    fi

    # homebrew jdks
    for jdk in /opt/homebrew/opt/openjdk*; do
      echo "Adding homebrew jdk at $jdk"
      jenv add $jdk
    done
  fi

  _setup_jdk_aliases

  # set the global to be the newest. note that this won't be an ea release because that
  # is excluded above in the major version  
  echo "loaded jdks: $_jdk_major_versions"
  echo "setting global jdk to $_latest_jdk_version"
  jenv global $_latest_jdk_version

}
alias reload-jenv='_reload_jenv'

# vi: set tabstop=2 shiftwidth=2 filetype=zsh expandtab:
