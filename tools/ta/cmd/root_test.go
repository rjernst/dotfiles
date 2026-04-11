package cmd

import (
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"testing"
)

// TestHelperProcess is not a real test. It is invoked by tests that need to
// exercise code paths that call os.Exit (e.g. unknown subcommand). The outer
// test runs this binary as a subprocess with GO_WANT_HELPER_PROCESS=1.
func TestHelperProcess(t *testing.T) {
	if os.Getenv("GO_WANT_HELPER_PROCESS") != "1" {
		return
	}
	// Extract args after the "--" separator injected by the caller.
	args := os.Args
	for len(args) > 0 {
		if args[0] == "--" {
			args = args[1:]
			break
		}
		args = args[1:]
	}
	// Mirror main.go: print errors to stderr and exit 1.
	root := NewRootCmd()
	root.SetArgs(args)
	if err := root.Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "ta: %v\n", err)
		os.Exit(1)
	}
}

func TestUnknownSubcommandExitsWithError(t *testing.T) {
	cmd := exec.Command(os.Args[0], "-test.run=TestHelperProcess", "--", "nonexistent-subcommand-xyz")
	cmd.Env = append(os.Environ(), "GO_WANT_HELPER_PROCESS=1")
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	err := cmd.Run()
	if err == nil {
		t.Fatal("expected non-zero exit, got exit 0")
	}
	exitErr, ok := err.(*exec.ExitError)
	if !ok {
		t.Fatalf("expected *exec.ExitError, got %T: %v", err, err)
	}
	if exitErr.ExitCode() != 1 {
		t.Errorf("expected exit code 1, got %d", exitErr.ExitCode())
	}
	if !strings.Contains(stderr.String(), "ta: unknown command 'nonexistent-subcommand-xyz'") {
		t.Errorf("unexpected stderr: %q", stderr.String())
	}
}
