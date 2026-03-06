# Package install commands per platform, used by _dotfiles_require_cmds
# to suggest how to install missing tools.
# Sourced by both zshrc (interactive shell) and setup (role provisioning).

typeset -gA _dotfiles_brew_pkg=(
  [jenv]="brew install jenv"
  [fnm]="brew install fnm"
  [tsh]="brew install --cask tsh"
  [kubectl]="brew install kubernetes-cli"
  [jq]="brew install jq"
  [vault]="brew install vault"
  [okta-awscli]="brew install okta-awscli"
  [starship]="brew install starship"
)
typeset -gA _dotfiles_pacman_pkg=(
  [jenv]="pacman -S jenv"
  [fnm]="pacman -S fnm"
  [tsh]="yay -S teleport-bin"
  [kubectl]="pacman -S kubectl"
  [jq]="pacman -S jq"
  [vault]="pacman -S vault"
  [starship]="pacman -S starship"
)
