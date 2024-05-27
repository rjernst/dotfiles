
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
  _jdk_major_versions=("${(@f)$(jenv versions | grep -E "^\s+((\d+((\.0)|(-ea))?)|(1.8))$" | sed -E 's/^ *//g' | sort -r --unique)}")
  _latest_jdk_version=
  for v in $_jdk_major_versions; do
    major=${v:0:(-2)}
    if [ "$v" = '1.8' ]; then
      major=8
    elif [[ $v == *-ea ]]; then
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
  if [[ $OSTYPE == linux* ]]; then
    for jdk in /usr/lib/jdk/*; do
      echo "Adding jdk at $jdk"
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

alias install-jdk='_install_jdk'
function _install_jdk() {
  if [ $# -eq 0 ]; then
    echo "Must provide jdk major version"
    return
  fi
  echo "Installing Oracle JDK $1"
  TMP_FILE=/tmp/jdk$1.tar.gz

  if [[ $OSTYPE == darwin* ]]; then
    OS_SUFFIX=macos-aarch64
    JDKS_DIR=/Library/Java/JavaVirtualMachines
  fi
  if [[ $OSTYPE == linux* ]]; then
    OS_SUFFIX=linux-x64
    JDKS_DIR=/usr/lib/jdk
  fi
  DOWNLOAD_URL="https://download.oracle.com/java/$1/latest/jdk-${1}_${OS_SUFFIX}_bin.tar.gz"
  echo "Downloading $DOWNLOAD_URL"
  curl -o $TMP_FILE $DOWNLOAD_URL
  if [ $? -ne 0 ]; then
    echo "Failed to download jdk $1"
    return
  fi
  sudo tar -xvzf $TMP_FILE -C $JDKS_DIR
  if ( $? -ne 0 ]; then
    echo "Failed to install jdk to $JDKS_DIR"
    return
  fi
  rm $TMP_FILE
}

# vi: set tabstop=2 shiftwidth=2 filetype=zsh expandtab:
