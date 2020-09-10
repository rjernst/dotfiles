osascript <<EOD
tell application "Safari"
  activate
  delay 1
end tell

tell application "System Events"
  tell process "Safari"
    set frontmost to true
    keystroke "," using {command down}
    delay 1

    tell window 1
      click button "AutoFill" of toolbar 1
      delay 0.5

      -- don't allow any autofill
      tell checkbox "Using information from my contacts" of group 1 of group 1 to if value is 1 then click
      tell checkbox "User names and passwords" of group 1 of group 1 to if value is 1 then click
      tell checkbox "Credit cards" of group 1 of group 1 to if value is 1 then click
      tell checkbox "Other forms" of group 1 of group 1 to if value is 1 then click
    end tell

    keystroke "w" using {command down}
  end tell
end tell
EOD
