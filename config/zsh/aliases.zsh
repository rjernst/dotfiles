
alias cfg='git -C $DOTFILES'

if [[ "$(uname)" == "Linux" ]]; then
  alias ls='ls --group-directories-first'
fi
alias ll='ls -l'
alias rm='rm -i'       # always ask before removing a file
alias mv='mv -i'       # always ask before moving a file onto another file
alias cp='cp -i'       # always ask before copying a file onto another file
alias h='history'
alias hs='history | grep -i'
alias disk='du -d 1 -x | sort -r -n | awk '\''{split("k m g",v); s=1;
while($1>1024){$1/=1024; s++} print int($1)" "v[s]"\t"$2}'\'
alias grep='grep --color=auto'
alias py3='python3'
alias g='git'
alias vm='vagrant'

alias gw='$DOTFILES/scripts/gradlew.sh'

[ -f $ZSHFILES/aliases.local ] && source $ZSHFILES/aliases.local
[ -f $ZSHFILES/aliases.machine ] && source $ZSHFILES/aliases.machine
[ -f $ZSHFILES/aliases.platform ] && source $ZSHFILES/aliases.platform

alias reload-config='echo "Reloading $HOME/.zshrc"; source $HOME/.zshrc'
alias cdd='cd $DOTFILES'
if [ -d $HOME/.prefs ]; then
  function _reload_prefs() {
    echo "Running scripts in $HOME/.prefs"
    for file in $HOME/.prefs/*.sh; do
      echo "$(basename $file)"
      zsh $file
    done
  }
  alias reload-prefs='_reload_prefs'

  function _reload_bundles() {
    echo "Updating homebrew bundles from $HOME/.Brewfile"
    brew bundle --global install 
  }
  alias reload-bundles='_reload_bundles'
fi

alias yubikey-enable='ykman config usb --enable otp'
alias yubikey-disable='ykman config usb --disable otp'

# vi: set tabstop=2 shiftwidth=2 filetype=sh expandtab:
