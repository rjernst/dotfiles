
# install jenv
export PATH="$HOME/.jenv/bin:$PATH"
_init_jenv() {
  eval "$(jenv init -)"
}
_init_jenv

# shortcut to current java home
alias jdk='readlink $(jenv javahome)'

if [ $PLATFORM = "macos" ]; then
  local jdk_base=/Library/Java/JavaVirtualMachines
  local jdk_suffix="Contents/Home"
fi

function _reload_jenv() {
  rm -rf $HOME/.jenv
  _init_jenv
  for jdk in $jdk_base/*; do
    echo "Adding jdk at $jdk"
    jenv add $jdk/$jdk_suffix
  done

  # create a shortcut for setting to each major version
  local major_versions=("${(@f)$(jenv versions | grep -E '\d+' | grep -E -v '^\W+[a-z]' | sed -E 's/.* ([0-9]+)\.0$/\1/' | grep -E -v '\W' | sort -r --unique)}")
  for v in $major_versions; do
    alias jdk${v}='jenv shell '${v}
  done

  # set the global to be the newest. note that this won't be an ea release because that
  # is excluded above in the major version  
  jenv global ${major_versions[1]}
}
alias reload-jenv='_reload_jenv'

# vi: set tabstop=2 shiftwidth=2 filetype=zsh expandtab:
