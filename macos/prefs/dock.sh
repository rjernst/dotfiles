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

# based on https://jessicadeen.com/show-hide-menubar-big-sur/
# anchors based on https://macscripter.net/viewtopic.php?pid=203573

# TODO:
# - Enable time machien in menu bar
# - disable spotlight in menu bar
# - Select 24 hour clock
# - Disable battery in menu bar
# - Disable wifi in menu bar
# - disable recent applications in dock

osascript <<EOD

try
  if application "System Preferences" is running then do shell script "killall 'System Preferences'"
end try
repeat until application "System Preferences" is not running
  delay 0.1
end repeat
tell application "System Preferences"
	reveal anchor "Spotlight" of pane "com.apple.preference.dock"
end tell

tell application "System Events"
	tell process "System Preferences"
    UI elements
    (*repeat while not (exists of UI element "Show in Menu Bar" of window "Dock & Menu Bar")
      delay 0.1
		end repeat
		tell window "Dock & Menu Bar"
			tell UI element "Show in Menu Bar" to if value is 1 then click
    end tell*)
	end tell
end tell

quit application "System Preferences"

EOD
