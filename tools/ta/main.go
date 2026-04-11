package main

import (
	"fmt"
	"os"

	"dotfiles/tools/ta/cmd"
)

func main() {
	if err := cmd.NewRootCmd().Execute(); err != nil {
		fmt.Fprintf(os.Stderr, "ta: %v\n", err)
		os.Exit(1)
	}
}
