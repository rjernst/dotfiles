osascript <<EOD

tell application "System Preferences"
  activate
  set current pane to pane "com.apple.preference.battery"
end tell

tell application "System Events"
  tell process "System Preferences"
    repeat until exists window "Battery"
    end repeat
    tell window "Battery"

      select row 2 of table 1 of scroll area 1
      
      tell group 1
        tell checkbox "Show battery status in menu bar" to if value is 1 then click
      end tell

    end tell
  end tell
end tell

quit application "System Preferences"

EOD
