package cmd

import (
	"dotfiles/tools/internal/dispatch"

	"github.com/spf13/cobra"
)

func NewRootCmd() *cobra.Command {
	root := &cobra.Command{
		Use:   "ta",
		Short: "Terminal Agent tool",
		// Disable default error behavior so we can handle unknown subcommands ourselves.
		SilenceErrors: true,
		SilenceUsage:  true,
	}

	root.AddCommand(NewVersionCmd())

	// Shell fallback: exec ta-<subcommand> for any unrecognized subcommand.
	root.RunE = func(cmd *cobra.Command, args []string) error {
		if len(args) == 0 {
			return cmd.Help()
		}
		return dispatch.ShellFallback(args[0], args[1:])
	}

	// Allow arbitrary args so cobra doesn't reject unrecognized subcommand names
	// before our RunE shell fallback can handle them.
	root.Args = cobra.ArbitraryArgs

	// Disable flag parsing on the root command so that flags like --target
	// are passed through as raw args to RunE (and then to the shell fallback)
	// rather than being rejected by cobra's flag parser.
	root.DisableFlagParsing = true

	// TraverseChildren makes cobra walk the arg list and run a matching child
	// command if found; unmatched args fall through to the root RunE above.
	root.TraverseChildren = true

	return root
}
