osascript <<EOD

tell application "System Preferences"
	activate
	set current pane to pane "com.apple.preference.keyboard"
end tell

tell application "System Events"
  tell process "System Preferences"
    tell window "Keyboard"

      wait 2
      click button "Modifier Keys…" of tab group 1
      
      click pop up button 2 of sheet 1 of window "Keyboard"
      click menu item 2 of menu 1 of pop up button 4 of sheet 1 of window "Keyboard"
    end tell
  end tell
end tell

(*
quit application "System Preferences"
*)

EOD
