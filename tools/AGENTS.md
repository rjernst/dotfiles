# Go Tools Conventions

This document defines the conventions for Go code in `tools/`. All agents and contributors working here must follow these rules.

## Module

Module path: `dotfiles/tools`

All imports of internal packages use this prefix, e.g. `dotfiles/tools/internal/foo`.

## `cmd/` Purity Rule

The `cmd/` directory is **strictly for command definitions and wiring**. No helpers, utilities, or business logic may live in `cmd/`. Those belong in `internal/` or other packages outside `cmd/`.

- **Allowed in `cmd/`**: cobra command construction, flag definitions, `AddCommand()` calls, thin glue that calls into `internal/`.
- **Not allowed in `cmd/`**: file I/O, network calls, string manipulation, anything that could be unit-tested independently of cobra.

## File Conventions

- **`cmd/root.go`** — one per binary. Creates the root `*cobra.Command` and adds top-level subcommands.
- **`cmd/<group>/dispatch.go`** — one per subcommand group subdirectory. Creates the group command and wires its children via `AddCommand()`.
- **One file per leaf command** — each exports a `NewXxxCmd() *cobra.Command` constructor. The file is named after the command (e.g. `version.go` for `ta version`).

### Directory Structure Mirrors Command Hierarchy

```
ta/cmd/root.go              → ta (root)
ta/cmd/version.go           → ta version
ta/cmd/wt/dispatch.go       → ta wt (future)
ta/cmd/wt/create.go         → ta wt create (future)
```

## No `init()` Registration

All command wiring is **explicit**. Never use `init()` to register commands. Instead, call `AddCommand()` in `root.go` or `dispatch.go` constructors. This makes the dependency graph clear and avoids import-order surprises.

## Build

**Always use the Makefile** — never run `go build` directly. The Makefile injects `BuildHash` and `BuildDate` via `-ldflags`. Binaries built without the Makefile will report `build dev, dev` for the version.

```
make -C tools ta          # build ta to ~/bin/ta
make -C tools all         # build all binaries
BUILD_DIR=dist make -C tools all   # build to ./dist/
make -C tools list        # print BINARIES (for CI)
make -C tools clean       # remove built binaries from BUILD_DIR
```

## Tests

- Run tests from `tools/`: `go test ./...`
- Test files live alongside source (e.g. `ta/cmd/version_test.go`).
- Use the Go stdlib `testing` package. No external test frameworks.
- Linting/vet: `go vet ./...` must pass.
