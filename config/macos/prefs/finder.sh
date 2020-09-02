osascript <<EOD
tell application "Finder"
	activate
end tell

tell application "System Events"
	tell process "Finder"
		keystroke "," using {command down}
		delay 1
		set hostname to do shell script "scutil --get HostName"
		set username to do shell script "echo $USER"

		tell window "Finder Preferences"
			click button "General" of toolbar 1
			delay 0.5

			-- show nothing on desktop
			tell checkbox "Hard disks" to if value is 1 then click
			tell checkbox "External disks" to if value is 1 then click
			tell checkbox "CDs, DVDs, and iPods" to if value is 1 then click
			tell checkbox "Connected servers" to if value is 1 then click

			tell pop up button 1
				click
				delay 0.2
				click menu item username of menu 1
			end tell
		end tell

		tell window "Finder Preferences"
			click button "Sidebar" of toolbar 1
			delay 0.5

			tell checkbox "Recents" to if value is 1 then click
			tell checkbox "AirDrop" to if value is 1 then click
			tell checkbox "Applications" to if value is 0 then click
			tell checkbox "Desktop" to if value is 0 then click
			tell checkbox "Documents" to if value is 0 then click
			tell checkbox "Downloads" to if value is 0 then click
			tell checkbox "Movies" to if value is 1 then click
			tell checkbox "Music" to if value is 1 then click
			tell checkbox "Pictures" to if value is 1 then click
			tell checkbox hostname to if value is 1 then click

			tell checkbox "iCloud Drive" to if value is 1 then click

			tell checkbox username to if value is 0 then click
      -- hard disk is special because it starts out in a partial state
			tell checkbox "Hard disks" to click
			tell checkbox "Hard disks" to if value is 0 then click
			tell checkbox "External disks" to if value is 0 then click
			tell checkbox "CDs, DVDs, and iOS Devices" to if value is 0 then click
			tell checkbox "Bonjour computers" to if value is 0 then click
			tell checkbox "Connected servers" to if value is 0 then click

			tell checkbox "Recent Tags" to if value is 1 then click

		end tell

		tell window "Finder Preferences"
			click button "Advanced" of toolbar 1
			delay 0.5

			tell checkbox "Show all filename extensions" to if value is 0 then click
			tell checkbox "Show warning before changing an extension" to if value is 0 then click
			tell checkbox "Show warning before removing from iCloud Drive" to if value is 0 then click
			tell checkbox "Show warning before emptying the Trash" to if value is 0 then click
			tell checkbox "Remove items from the Trash after 30 days" to if value is 1 then click
		end tell

		keystroke "w" using {command down}
	end tell
end tell
EOD
