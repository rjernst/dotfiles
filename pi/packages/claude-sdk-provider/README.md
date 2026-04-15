# claude-sdk-provider

A [pi](https://github.com/badlogic/pi-mono) extension that routes model execution through the Claude Agent SDK, enabling Claude subscription-backed usage instead of the normal Anthropic API billing path.

When authenticated with a Claude subscription (OAuth), use this provider to route requests through your subscription instead of incurring per-token API costs.

---

## What problem does this solve?

The built-in Anthropic provider in pi uses `ANTHROPIC_API_KEY` for all requests, which charges per-token against the Anthropic API. If you have a Claude Pro/Max subscription (accessed through Claude Code via OAuth), using an OAuth token with the regular API still incurs API billing.

This extension registers a `claude-sdk` provider that routes requests through the Claude Agent SDK instead, which uses your subscription — avoiding per-token API costs.

- Uses the Claude Agent SDK for model execution (subscription-billed)
- Exposes a fixed set of Claude model ids under the `claude-sdk` provider
- Leaves pi in control of all tools, file editing, bash execution, and UI

---

## Setup

### 1. Install

```bash
npm install --prefix pi/packages/claude-sdk-provider
```

> **Note:** The `postinstall` script (`scripts/fix-typescript.cjs`) automatically repairs the TypeScript compiler installation if npm's tar extraction corrupts the binary (a known issue in some sandboxed Linux environments). The script detects corruption by running `tsc --version`, then re-downloads and re-extracts a fresh tarball via `npm pack` if needed.

### 2. Authenticate

Ensure you are logged in to Claude Code with OAuth (subscription) before starting pi:

```bash
claude auth status   # verify you are logged in
```

### 3. Load the extension

```bash
pi -e /path/to/pi/packages/claude-sdk-provider
```

Or use the direct path relative to this repo:

```bash
pi -e $DOTFILES/pi/packages/claude-sdk-provider
```

### 4. Select a model

Once pi starts, use `/model` to switch to the `claude-sdk` provider:

```
/model claude-sdk
```

Then pick one of the available models:

| Model ID | Description |
|---|---|
| `claude-opus-4-5` | Most capable, best for complex tasks |
| `claude-sonnet-4-5` | Balanced performance and speed |
| `claude-haiku-4-5` | Fastest, best for simple tasks |

---

## v1 scope

- **Context source:** pi's normal system prompt / AGENTS / skills flow — no `.claude/` config loading
- **Tools:** pi built-in tools only (Read, Write, Edit, Bash, Grep, Glob); Claude proposes tool calls, pi executes them
- **SDK-side execution:** denied — Claude proposes tool calls, pi executes them
- **Custom tools / MCP bridge:** out of scope for v1

---

## Authentication

This provider requires OAuth authentication via Claude Code (`claude auth login`). If you're authenticated with an API key instead, use the built-in Anthropic provider.

---

## Development

### Run typecheck

```bash
npm run typecheck --prefix pi/packages/claude-sdk-provider
```

### Run tests

```bash
npm test --prefix pi/packages/claude-sdk-provider
```

Tests use mocked SDK interactions and do not require live Claude subscription auth.

---

## Implementation references

When contributing to this extension, consult these package-local references in `node_modules/`:

- `node_modules/@mariozechner/pi-coding-agent/README.md`
- `node_modules/@mariozechner/pi-coding-agent/docs/extensions.md`
- `node_modules/@mariozechner/pi-coding-agent/docs/custom-provider.md`
- `node_modules/@mariozechner/pi-coding-agent/docs/sdk.md`
- `node_modules/@mariozechner/pi-coding-agent/docs/models.md`
- `node_modules/@mariozechner/pi-coding-agent/examples/extensions/custom-provider-anthropic/index.ts`
- `node_modules/@mariozechner/pi-coding-agent/examples/extensions/custom-provider-gitlab-duo/index.ts`
- `node_modules/@mariozechner/pi-coding-agent/examples/extensions/custom-provider-qwen-cli/index.ts`

These local files are the authoritative pi extension reference. An autonomous agent implementing or modifying this extension can do so entirely from these local docs without any prior pi-extension knowledge.
