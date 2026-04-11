// Package dispatch provides a bridge that execs legacy ta-<subcommand> shell
// scripts. It will be removed once all subcommands are native Go.
package dispatch

import (
	"fmt"
	"os"
	"path/filepath"
	"syscall"
)

// ShellFallback looks for ta-<subcommand> next to the running binary and execs
// it, replacing the current process. It returns an error only if the subcommand
// is not found; a successful exec never returns.
func ShellFallback(subcommand string, args []string) error {
	exe, err := os.Executable()
	if err != nil {
		return fmt.Errorf("could not determine executable path: %w", err)
	}
	// Resolve symlinks so binDir is the real directory, not a symlink's parent.
	exe, err = filepath.EvalSymlinks(exe)
	if err != nil {
		return fmt.Errorf("could not resolve executable path: %w", err)
	}
	binDir := filepath.Dir(exe)
	script := filepath.Join(binDir, "ta-"+subcommand)

	info, err := os.Stat(script)
	if err != nil || info.IsDir() || info.Mode()&0o111 == 0 {
		return fmt.Errorf("unknown command '%s'", subcommand)
	}

	argv := append([]string{script}, args...)
	if execErr := syscall.Exec(script, argv, os.Environ()); execErr != nil {
		return fmt.Errorf("exec %s: %w", script, execErr)
	}
	return nil // unreachable after syscall.Exec
}
