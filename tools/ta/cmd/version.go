package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

// BuildHash is injected at build time via -ldflags -X.
var BuildHash = "dev"

// BuildDate is injected at build time via -ldflags -X.
var BuildDate = "dev"

func NewVersionCmd() *cobra.Command {
	return &cobra.Command{
		Use:   "version",
		Short: "Print version information",
		Args:  cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			fmt.Fprintf(cmd.OutOrStdout(), "ta (build %s, %s)\n", BuildHash, BuildDate)
			return nil
		},
	}
}
