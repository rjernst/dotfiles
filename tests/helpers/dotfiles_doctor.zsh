#!/usr/bin/env zsh
# Helper: sources the doctor plugin and runs a single check function.
# Expects: HOME, DOTFILES, ZDOTDIR set by caller.
# Usage: zsh dotfiles_doctor.zsh <check_function_name>
# Prints: function output, then ERRORS=N and WARNINGS=N.

# Source the plugin to define all functions
source "$DOTFILES/zsh/plugins/dotfiles-doctor.zsh"

local _doctor_errors=0
local _doctor_warnings=0

# Run the requested check function
$1

echo "ERRORS=$_doctor_errors"
echo "WARNINGS=$_doctor_warnings"
