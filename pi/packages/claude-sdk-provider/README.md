# claude-sdk-provider

A [pi](https://github.com/badlogic/pi-mono) extension that routes model execution through the Claude Agent SDK, enabling Claude subscription-backed usage instead of the normal Anthropic API billing path.

This provider is **subscription-only** and **fail-closed**: if Claude SDK subscription auth is unavailable, it errors immediately with a clear message and never silently falls back to `ANTHROPIC_API_KEY` or any other metered API path.

---

## What problem does this solve?

The built-in Anthropic provider in pi uses `ANTHROPIC_API_KEY` for all requests, which charges per-token against the Anthropic API. If you have a Claude Pro/Max subscription (accessed through Claude Code via OAuth), using an OAuth token with the regular API still incurs API billing.

This extension registers a `claude-sdk` provider that routes requests through the Claude Agent SDK instead, which uses your subscription — avoiding per-token API costs.

- Uses the Claude Agent SDK for model execution (subscription-billed)
- Exposes Claude model ids under the `claude-sdk` provider (configurable via `models.json`)
- Leaves pi in control of all tools, file editing, bash execution, and UI
- Costs are reported as zero (subscription usage is not billed per-token)

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
claude auth login    # authenticate with your Claude subscription
claude auth status   # verify you are logged in
```

Do **not** set `ANTHROPIC_API_KEY` for this provider. If an API key is detected, the provider will reject the request with an error.

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
| `claude-opus-4-6` | Most capable, best for complex tasks |
| `claude-sonnet-4-6` | Balanced performance and speed |
| `claude-haiku-4-5` | Fastest, best for simple tasks |

To update available models (e.g. after a new Claude release), edit `models.json` at the package root — no recompile needed.

---

## Architecture

### Persistent SDK session

The provider uses a **persistent subprocess session** for efficiency. Rather than spawning a new Claude Code subprocess on every turn (with process boot, authentication, and shutdown overhead each time), the SDK subprocess starts once on first use and stays alive for the duration of the pi session.

```
pi session
  │
  ├─ turn 1 → SdkSession.send(msg) → starts subprocess via query(prompt: AsyncIterable)
  │            ← streams response events ← SDK subprocess (stays alive)
  │
  ├─ turn 2 → SdkSession.send(msg) → pushes message via query.streamInput()
  │            ← streams response events ← same subprocess
  │
  └─ ... subsequent turns reuse the same subprocess
```

Key design points:
- **Start once, reuse across turns** — the subprocess starts on first `send()` and persists until `close()` or process exit
- **Incremental messages** — pi sends only the new user message or tool results each turn; the SDK subprocess maintains conversation history internally
- **`systemPrompt`** — set to pi's system prompt, which **replaces** the default Claude Code system prompt entirely (no CLAUDE.md bleed-through)
- **`tools`** — set to active pi built-in tool names in Claude format (e.g. `['Read', 'Edit', 'Bash']`)
- **`maxTurns: 1`** — every turn returns to pi; Claude proposes tool calls, pi executes them
- **`persistSession: false`** — prevents the SDK from writing `.claude` session files
- **Session recreation** — the provider detects conversation resets (message count decrease) and model changes, closing and recreating the session as needed

### MCP server passthrough

MCP server configs can be forwarded to the SDK subprocess via an optional `mcp-servers.json` file in the package root. The SDK handles MCP server connections internally, making MCP tools visible to Claude alongside pi's built-in tools.

See `mcp-servers.json.example` for configuration format (supports stdio, SSE, and HTTP transport types). The file is gitignored since it may contain auth tokens.

---

## v1 scope

- **Auth:** subscription-only, fail-closed — no API key fallback
- **Context source:** pi's normal system prompt / AGENTS / skills flow — no `.claude/` config loading
- **Tools:** pi built-in tools only (Read, Write, Edit, Bash, Grep, Glob); Claude proposes tool calls, pi executes them
- **SDK-side execution:** denied (`maxTurns: 1`) — Claude proposes tool calls, pi executes them
- **MCP:** static passthrough via `mcp-servers.json` (optional); pi-native MCP bridging out of scope for v1

---

## Authentication and failure modes

This provider requires OAuth authentication via Claude Code (`claude auth login`).

**If subscription auth is missing**, the provider returns an error with this message:

> Claude SDK subscription auth is required but unavailable. This provider uses the Claude Agent SDK which requires an active Claude subscription (Pro/Max) authenticated via Claude Code. Run `claude auth login` to authenticate with your subscription. This provider intentionally does not fall back to ANTHROPIC_API_KEY or any other metered API billing path.

**If an API key is detected** (e.g. `ANTHROPIC_API_KEY` is set), the provider rejects the request immediately to prevent accidental billing:

> claude-sdk provider received an API key, but this provider is subscription-only. Do not set ANTHROPIC_API_KEY or any apiKey for this provider. Claude SDK subscription auth is required but unavailable. ...

If you want to use API key billing instead, use the built-in Anthropic provider (`/model anthropic`).

---

## Manual smoke test

These steps verify the provider works end-to-end on a host machine with a valid Claude subscription and a working pi installation.

### Prerequisites

- pi is installed and working (`pi --help`)
- Claude Code is installed (`claude --version`)
- You are authenticated with a Claude subscription (`claude auth status` shows logged in)
- The package is installed (`npm install --prefix pi/packages/claude-sdk-provider`)

### Steps

1. **Start pi with the extension loaded:**
   ```bash
   pi -e pi/packages/claude-sdk-provider
   ```

2. **Verify provider registration** — run `/model` in pi and confirm `claude-sdk` appears as a provider with three models: `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5`.

3. **Select a model:**
   ```
   /model claude-sdk/claude-sonnet-4-6
   ```

4. **Send a simple text prompt** — type a short message (e.g. "What is 2+2?") and verify:
   - A text response streams back without errors
   - No API key billing warning appears
   - The response completes with a `stop` reason

5. **Test tool usage** — ask Claude to perform a tool action (e.g. "List the files in the current directory") and verify:
   - Claude proposes a tool call (e.g. `Glob` or `Bash`)
   - pi executes the tool (not Claude)
   - The tool result is displayed in pi's UI

6. **Verify fail-closed auth** — confirm the provider returns the subscription auth error when auth is unavailable. Note: `claude auth logout` is global and will affect all Claude Code sessions, so re-authenticate immediately after testing:
   ```bash
   claude auth logout
   pi -e pi/packages/claude-sdk-provider
   # Select /model claude-sdk/claude-sonnet-4-6, send a message
   # Expected: clear error about missing subscription auth
   # Re-authenticate immediately:
   claude auth login
   ```

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
