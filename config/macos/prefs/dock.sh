# reset to default state
defaults delete com.apple.dock

######### make the dock effectively disappear ##########
# only active apps
defaults write com.apple.dock static-only -bool true
# icon size to infinitesimal
defaults write com.apple.dock tilesize -int 1
defaults write com.apple.dock orientation left
defaults write com.apple.dock autohide -int 1
# auto hide immediately
defaults write com.apple.dock autohide-time-modifier -int 0

# restart the dock
killall Dock
