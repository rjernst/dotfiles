osascript <<EOD

tell application "System Preferences"
  activate
  set current pane to pane "com.apple.preference.datetime"
end tell

tell application "System Events"
  tell process "System Preferences"
    repeat until exists window "Date & Time"
    end repeat
    tell window "Date & Time"
      
      tell tab group 1
        click radio button "Clock"

        repeat until exists checkbox "Show date and time in menu bar"
        end repeat

        tell checkbox "Show date and time in menu bar" to if value is 1 then click

      end tell
    end tell
  end tell
end tell

quit application "System Preferences"

EOD
