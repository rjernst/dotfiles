package cmd

import (
	"bytes"
	"strings"
	"testing"
)

func TestVersionCmd(t *testing.T) {
	root := NewRootCmd()
	var buf bytes.Buffer
	root.SetOut(&buf)
	root.SetArgs([]string{"version"})
	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	got := strings.TrimSpace(buf.String())
	want := "ta (build dev, dev)"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}
