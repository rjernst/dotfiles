osascript <<EOD
tell application "System Preferences"
  activate
  set current pane to pane "com.apple.preference.general"
end tell

tell application "System Events"
  tell process "System Preferences"
    repeat until exists window "General"
    end repeat
    tell window "General"
      click checkbox "Dark"
    end tell
  end tell
end tell
EOD
