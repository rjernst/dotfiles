osascript <<EOD

tell application "System Preferences"
  activate
  set current pane to pane "com.apple.preference.energysaver"
end tell

tell application "System Events"
  tell process "System Preferences"
    repeat until exists window "Energy Saver"
    end repeat
    tell window "Energy Saver"
      
      tell checkbox "Show battery status in menu bar" to if value is 1 then click

    end tell
  end tell
end tell

quit application "System Preferences"

EOD
