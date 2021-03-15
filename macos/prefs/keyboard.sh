
# fast movement
defaults write -g KeyRepeat -int 2
defaults write -g InitialKeyRepeat -int 15

osascript <<EOD

tell application "System Preferences"
  activate
  set current pane to pane "com.apple.preference.keyboard"
end tell

tell application "System Events"
  tell process "System Preferences"
    repeat until exists window "Keyboard"
    end repeat
    tell window "Keyboard"
      click radio button "Keyboard" of tab group 1

      # caps lock -> ctrl
      click button "Modifier Keys…" of tab group 1
      repeat until exists sheet 1
      end repeat

      if exists pop up button "Select keyboard:" of sheet 1 then
        set keyboardDropDown to pop up button "Select keyboard:" of sheet 1

        -- open the drop down to extract the list of keyboard names
        click keyboardDropDown
        repeat until exists menu 1 of keyboardDropDown
        end repeat
        set keyboardNames to name of menu items in menu 1 of keyboardDropDown
        key code 53

        repeat with keyboard in keyboardNames
          -- reopen the drop down before selecting each one
          click keyboardDropDown
          repeat until exists menu 1 of keyboardDropDown
          end repeat

          -- select the keyboard and set the caps lock action
          click menu item keyboard of menu 1 of keyboardDropDown
          my setCapsLockToControl()
        end repeat
      else
        -- just one keyboard
        my setCapsLockToControl()
      end if
      
      click button "OK" of sheet 1

      tell tab group 1
        click radio button "Input Sources"
        repeat until exists checkbox "Show Input menu in menu bar"
        end repeat

        tell checkbox "Show Input menu in menu bar" to if value is 1 then click
      end tell

      click radio button "Text" of tab group 1

      tell tab group 1
        repeat until exists checkbox "Correct spelling automatically"
        end repeat

        tell checkbox "Correct spelling automatically" to if value is 1 then click
      end tell
      
    end tell
  end tell
end tell

quit application "System Preferences"

on setCapsLockToControl()
  tell application "System Events"
    tell process "System Preferences"
      tell window "Keyboard"

        -- open the drop down
        set capsLockDropDown to pop up button "Caps Lock (⇪) Key:" of sheet 1
        click capsLockDropDown
        repeat until exists menu 1 of capsLockDropDown
        end repeat

        click menu item "⌃ Control" of menu 1 of capsLockDropDown

      end tell
    end tell
  end tell
end setCapsLockToControl

EOD
