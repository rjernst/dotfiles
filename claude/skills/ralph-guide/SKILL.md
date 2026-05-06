---
name: ralph-guide
description: >-
  Reference guide for Ralph, the dockerized AI coding loop, and its sandbox/agent-loop
  configuration. TRIGGER when: the user asks about ralph, agent loop, sandbox configuration,
  .agent-loop directory, sandbox dependencies, sandbox images, or how Ralph executes specs.
  DO NOT TRIGGER when: the user invokes /create-spec (that has its own skill) or is just
  running ralph commands without asking questions about how it works.
---

You are a reference guide for Ralph and the agent-loop sandbox system. When the user asks
questions about Ralph, answer using the information below. Do not guess — if the question
isn't covered here, say so and suggest checking the source code.

## What is Ralph?

Ralph is a GitHub Issues-driven AI coding loop. It reads spec issues from GitHub, executes
them inside isolated sandboxes (Docker containers or Tart VMs), and commits the results.

### Key commands

| Command | Purpose |
|---------|---------|
| `ralph --issue <number>` | Execute a single spec issue |
| `ralph --poll` | Poll for `status:ready` issues and execute them |
| `ralph --poll --interval 10s` | Custom poll interval (default: 30s) |
| `ralph --poll --timeout 2h` | Poll with deadline |
| `ralph --model <model>` | Use a specific Claude model (default: sonnet) |
| `ralph --push` | Git push after each iteration |
| `ralph --rebuild` | Force re-pull base image and rebuild sandbox |
| `ralph --agent <name>` | Use a specific agent (default: claude) |
| `ralph selftest` | Smoke test the full pipeline |

### How it works

1. Ralph reads a spec issue (markdown with YAML frontmatter specifying `branch` and optional `base`)
2. Creates/reuses an isolated sandbox (Docker container or Tart VM)
3. Injects the spec as `/tmp/spec.md` inside the sandbox
4. Runs Claude Code inside the sandbox, which implements ONE task per iteration
5. Syncs commits back to the host worktree
6. Repeats until all tasks are done or a task is blocked

## Sandbox Configuration (`.agent-loop/` directory)

Each project can customize its sandbox via files in a `.agent-loop/` directory at the project root.

### Runtime type (`.agent-loop/config.json`)

```json
{"type": "docker-sandbox"}
```

Valid types: `docker-sandbox` (default), `docker-container`, `tart`.

For Tart VMs, additional fields: `base_image` (required), `cpu`, `memory_gb`.

### Extra packages (`.agent-loop/dependencies`)

A plain text file listing Debian/Ubuntu apt packages to install in the sandbox, one per line.
Comments (`#`) and blank lines are allowed.

```
# Example: add pytest and Java to the sandbox
python3-pytest
openjdk-21-jdk
```

Ralph auto-generates a Dockerfile layer that installs these packages on top of the base
sandbox image. The resulting project image is cached and tagged with a content hash, so it
only rebuilds when the dependencies file changes.

**Package naming rules:** must match dpkg naming conventions (lowercase alphanumerics, `+`,
`-`, `.`). Architecture qualifiers (e.g., `pkg:amd64`) and version pins (e.g., `pkg=1.2.3`)
are supported.

### Custom Dockerfile (`.agent-loop/Dockerfile.sandbox`)

For more control than the dependencies file, provide a full Dockerfile. It receives the base
image as a build arg:

```dockerfile
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pytest \
    && rm -rf /var/lib/apt/lists/*
# Custom setup here
USER agent
```

**Precedence:** `Dockerfile.sandbox` takes priority over `dependencies` if both exist.

### Allowed hosts (`.agent-loop/config.json`)

To allow network access from the sandbox (which is network-isolated by default), add
`allowed_hosts` to `config.json`:

```json
{
  "type": "docker-sandbox",
  "allowed_hosts": ["registry.npmjs.org", "pypi.org"]
}
```

## Image Architecture

Ralph uses a two-tier image system:

1. **Base image** (`docker/agent-loop/<agent>/Dockerfile` in dotfiles) — shared across all
   projects. Includes the agent runtime and common tools. Re-pulled if older than 7 days.
2. **Project image** (from `.agent-loop/dependencies` or `Dockerfile.sandbox`) — per-project
   layer built on top of the base image. Cached with a content-addressed tag.

Image tags follow the pattern:
- Base: `agent-loop-sandbox-<agent>:v<hash>`
- Project: `agent-loop-sandbox-<agent>-<project>:v<hash>`

## Spec Issue Format

Spec issues use this structure:

```markdown
---
branch: feature-branch-name
base: main                    # optional, defaults to repo default branch
depends: [11, 17]             # optional, issue numbers this spec depends on
---
# Spec: Feature Name

## Overview
...

## Implementation Plan

### Step 1: First task
**Files:** ...
**Implement:** ...
**Acceptance:** ...

### Step N: Run all checks
**Acceptance:**
- All tests pass clean
```

Labels: `spec` + `status:ready` / `status:in-progress` / `status:done` / `status:needs-attention` / `status:blocked`

Use `/create-spec` to generate spec issues interactively.

$ARGUMENTS
