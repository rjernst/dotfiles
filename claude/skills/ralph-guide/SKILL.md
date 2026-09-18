---
name: ralph-guide
description: >-
  Reference guide for Ralph, the dockerized AI coding loop, and its sandbox/agent-loop
  configuration. TRIGGER when: the user asks about ralph, agent loop, sandbox configuration,
  .agent-loop directory, sandbox dependencies, sandbox images, runtime backends (docker,
  tart, nono), or how Ralph executes specs.
  DO NOT TRIGGER when: the user invokes /create-spec (that has its own skill) or is just
  running ralph commands without asking questions about how it works.
---

You are a reference guide for Ralph and the agent-loop sandbox system. When the user asks
questions about Ralph, answer using the information below. Do not guess — if the question
isn't covered here, say so and suggest checking the source code.

## What is Ralph?

Ralph is a GitHub Issues-driven AI coding loop. It reads spec issues from GitHub, executes
them inside isolated sandboxes (Docker containers, Tart VMs, or host processes under nono),
and commits the results.

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
| `ralph selftest --runtime <type>` | Smoke test a specific runtime (`docker-sandbox`, `docker-container`, `tart`, `nono`) |
| `ralph prune-sandboxes` | Remove orphaned and stale sandboxes (also takes `--runtime`) |

### How it works

1. Ralph reads a spec issue (markdown with YAML frontmatter specifying `branch` and optional `base`)
2. Creates/reuses an isolated sandbox (Docker container, Tart VM, or nono profile)
3. Injects the spec as `/tmp/spec.md` inside the sandbox (the nono runtime uses a
   per-iteration directory in its state dir instead)
4. Runs Claude Code inside the sandbox, which implements ONE task per iteration
5. Syncs commits back to the host worktree
6. Repeats until all tasks are done or a task is blocked

## Sandbox Configuration (`.agent-loop/` directory)

Each project can customize its sandbox via files in a `.agent-loop/` directory at the project root.

### Runtime type (`.agent-loop/config.json`)

```json
{"type": "docker-sandbox"}
```

Valid types: `docker-sandbox` (default), `docker-container`, `tart`, `nono`.

For Tart VMs, additional fields: `base_image` (required), `cpu`, `memory_gb`.

For nono, additional field: `network` (see the nono runtime section below).

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

Both files are ignored by the `nono` runtime, which builds no image.

### Allowed hosts (`.agent-loop/config.json`)

To allow network access from the sandbox (which is network-isolated by default), add
`allowed_hosts` to `config.json`:

```json
{
  "type": "docker-sandbox",
  "allowed_hosts": ["registry.npmjs.org", "pypi.org"]
}
```

## The `nono` Runtime

The `nono` runtime has no container and no VM. The agent runs **directly on the host** as a
child of `nono run`, which applies kernel sandboxing (Seatbelt on macOS, Landlock + seccomp
on Linux) to that process. The host toolchain is used as is — whatever `java`, `node`, or
`python3` is on your `PATH` is what the agent gets.

```json
{
  "type": "nono",
  "network": "filtered",
  "allowed_hosts": ["registry.npmjs.org"]
}
```

**Requirements:**

- **nono ≥ 0.77.0** on `PATH` (see https://nono.sh/docs). Older versions lack the profile's
  `cmd://` credential capture and the `--allow-unix-socket` flag ralph passes to `nono run`.
- `claude` on `PATH`. This runtime supports **only** `--agent claude`; any other agent
  exits 2 with `ralph: runtime nono only supports agent claude`.
- `docker` on `PATH`, for the Docker socket proxy.
- On **Linux**, `secret-tool` (from `libsecret`) for the token store. macOS uses the
  built-in `security` keychain command. Missing binary →
  `ralph: secret-tool not found (install libsecret)`.

**No image is built.** `.agent-loop/dependencies` and `.agent-loop/Dockerfile.sandbox` are
ignored; if either exists, ralph prints
`ralph: warning: <path> is ignored by the nono runtime`. Install those tools on the host
instead, and grant any state directories they need via `.agent-loop/nono-profile.json`.

### `network`: `filtered` (default) or `unrestricted`

| Value | Behavior |
|-------|----------|
| `filtered` | Reachable domains are the agent's own (`api.anthropic.com`, `statsig.anthropic.com`, `sentry.io`) plus this project's `allowed_hosts`, plus the host's ephemeral port range on loopback (for test servers and Gradle daemons). **The loopback half of this works on macOS only** — see Known limitations. |
| `unrestricted` | No domain or port filtering; `nono run --allow-net`. Credential injection still applies. |

Any other value raises a `ValueError` from `load_runtime_config`.

The ephemeral port range is read from the host: `sysctl net.inet.ip.portrange.first/.last`
on macOS, `/proc/sys/net/ipv4/ip_local_port_range` on Linux. nono caps macOS port ranges at
16384 ports; a wider host range errors with
`ralph: ephemeral port range exceeds nono macOS limit of 16384`.

On Linux this range buys nothing in `filtered` mode: nono's seccomp layer allows outbound
connects to its own proxy port only, so a local client cannot reach a local server whatever
the profile grants. Use `"network": "unrestricted"` on Linux hosts. The selftest's
"loopback ports" check is what surfaces this — it binds *and* connects.

### What the sandbox can touch

Read-write: the worktree, the parts of the git common dir a commit writes
(`objects/`, `refs/`, `logs/`, and this worktree's own `worktrees/<name>/`),
`~/.ralph/claude-config` (and its `.lock`), and the per-iteration spec directory. Read-only:
the git common dir itself, `PATH` entries under `$HOME`, the directory holding the resolved
`claude` binary, and the generated gitconfig.

The git common dir root stays read-only on purpose: git runs hooks and honours config such as
`core.fsmonitor` on the **host**, outside the sandbox, so an agent able to write `.git/hooks`
or `.git/config` could run code nono never sees. The generated gitconfig sets `gc.auto=0` and
`maintenance.auto=false` to keep git from trying to repack, which would write that root. A
plain (non-worktree) checkout keeps HEAD and the index there and does need it read-write, but
ralph always runs the agent in a linked worktree.

The spec is granted as a *directory*, not a file: nono ties a file grant to the inode, so a
save that writes a temp file and renames it over the target — what Claude Code does — would
fail, and the agent could not tick off its own tasks.

The user's real `~/.claude`, `~/.claude.json`, `~/.gitconfig`, `~/.ssh`, the keychain, and
the raw `/var/run/docker.sock` are **never** granted read-write and never written. (A native
Claude Code install lives under `~/.claude/local`, so that one subdirectory does get a
*read* grant as the resolved `claude` binary's directory.) Git identity comes from a generated
`gitconfig` in the sandbox state dir passed as `GIT_CONFIG_GLOBAL`; ralph never runs
`git config --global` for this runtime. That config also turns signing off: the sandbox has
no key, and a commit claiming your signature would be worse than one without.

### Claude config dir

The agent gets `CLAUDE_CONFIG_DIR=~/.ralph/claude-config` — a dedicated config dir, not your
real `~/.claude`. If the agent needs onboarding (theme, trust prompt, etc.), run Claude once
against it by hand:

```sh
CLAUDE_CONFIG_DIR=~/.ralph/claude-config claude
```

### Credentials

nono's own per-session credential proxy injects the token. It fetches it host-side via a
`cmd://` capture that runs `ralph get-token`, so the token never enters the profile file and
the sandbox cannot read the token store. Ralph's TCP credential proxy is **not** started for
this runtime.

### Docker

Docker reaches the daemon through ralph's existing Docker socket proxy, listening on a Unix
socket at `~/.ralph/docker-proxy.sock` and granted with `--allow-unix-socket`. The agent gets
`DOCKER_HOST` set to `unix://` plus that socket path. Raw `docker.sock` is never granted.

### Project profile (`.agent-loop/nono-profile.json`)

For grants ralph cannot infer — build caches, toolchain managers, extra env vars — check a
raw nono profile into the project. Ralph copies it to `project.json` in the sandbox state
dir, beside the generated profile (rewriting `meta.name` to match), and sets
`"extends": "project"`. nono resolves `extends` against the profile's own directory first, so
nothing is installed globally and the copy goes when the sandbox does. Lists merge additively
per nono inheritance, so entries here **add** to ralph's grants.

```json
{
  "meta": {"name": "my-project", "version": "1.0.0"},
  "filesystem": {
    "allow": ["~/.gradle"],
    "read": ["~/.jenv"]
  },
  "environment": {
    "allow_vars": ["JAVA_HOME", "GRADLE_USER_HOME"]
  },
  "network": {
    "allow_domain": ["services.gradle.org", "repo1.maven.org"]
  }
}
```

`allow_vars` only lets a variable **pass through** from the host environment — it does not
set a value. To give the agent `FOO=bar`, export `FOO` on the host before running ralph and
list it in `allow_vars`. (`JAVA_HOME` above is already in ralph's default allow-list; it is
shown to illustrate the shape.)

This file is trusted, checked-in project config — exactly like `Dockerfile.sandbox` is for
the Docker backends. It can widen the sandbox arbitrarily, including re-granting `~/.claude`.
Review it as you would a Dockerfile.

Note that a filesystem grant covers the directory itself, not the targets of symlinks inside
it. A `~/bin` full of links into a dotfiles checkout needs that checkout granted here too.

### State layout

Per-sandbox state lives in the project's shared git directory, so it dies with the repository
and never shows up in `git status`. The sandbox cannot write any of it — that directory is
read-only to it.

```
<git-common-dir>/ralph/nono/<sandbox>/profile.json  generated nono profile
<git-common-dir>/ralph/nono/<sandbox>/project.json  project profile copy, if any
<git-common-dir>/ralph/nono/<sandbox>/gitconfig     GIT_CONFIG_GLOBAL for the agent
<git-common-dir>/ralph/nono/<sandbox>/worktree      worktree path, for pruning
<git-common-dir>/ralph/nono/<sandbox>/spec-XXXX/    per-iteration spec dir
~/.ralph/claude-config                              CLAUDE_CONFIG_DIR for the agent
~/.ralph/docker-proxy.sock                          filtered Docker API socket
```

The last two are shared by every project, so they stay under `~/.ralph`.

`ralph prune-sandboxes --runtime nono` removes state dirs whose worktree is gone or whose
timestamp is stale; it acts on the repo you run it in.

### Verifying

`ralph selftest --runtime nono` runs the whole pipeline through a generated profile in a temp
git repo: prerequisites and version, profile acceptance, credential injection (including a
real `claude -p` call), the credential capture command, that the real token is hidden from
the sandbox, network isolation, loopback binding, token store denial, Docker via the socket
proxy, that `~/.claude` and `~/.gitconfig` are unwritable, that `.git/hooks` and
`.git/config` are unwritable, that `git commit` works with the generated gitconfig and
produces an *unsigned* commit, and that the agent can save the spec back with an atomic
write. The temp repo it builds has a linked worktree, like a real run.

### Known limitations

- **Linux + filtered network breaks anything that talks to itself over loopback**
  (JVM/Gradle builds, test suites that start a local server). Workaround: set
  `"network": "unrestricted"` on Linux hosts. Upstream nono issues nolabs-ai/nono#1652,
  #1444, #1786, #1640 are all likely this. The mechanism, read from nono 0.77.0 source
  (not reproduced on a Linux host — macOS is unaffected):

  Filtered mode is nono's proxy mode, which layers a seccomp supervisor over Landlock.
  Landlock does allow connect and bind on every port of `open_port_range`
  (`crates/nono/src/sandbox/linux.rs:958`), but the seccomp layer above it allows a connect
  only to nono's own proxy: `sockaddr.is_loopback && sockaddr.port == config.proxy_port`
  (`crates/nono-cli/src/exec_strategy/supervisor_linux.rs:753`, and the policy comment at
  701). Both must hold, so a connect to `127.0.0.1:<some other port>` is denied even though
  Landlock permits it. `open_port_range` feeds only the *bind* side of that supervisor
  (`crates/nono-cli/src/supervised_runtime.rs:358`), which leaves a test able to open its
  listener and unable to connect back to it.

  There is no profile setting that fixes this. `open_port: 0`, the `localhost:*` wildcard,
  is macOS-only and Linux rejects it (`linux.rs:887`), and per-port grants cannot beat the
  seccomp layer regardless of how many are listed.

  The flip side: on Linux, Landlock port grants are port-only and cannot be scoped to an
  address at all — `--open-port 3000` permits port 3000 on *any* IP
  (`docs/cli/usage/flags.mdx:616`). So the ephemeral range would be an outbound hole were
  proxy mode not closing it. It is not one in filtered mode, and on macOS the rules are
  `localhost:N` and address-scoped already.
- **macOS JDK proxy connections** can fail (nolabs-ai/nono#1830). Workaround: export
  `JAVA_TOOL_OPTIONS=-Djava.net.preferIPv4Stack=true` on the host and add
  `JAVA_TOOL_OPTIONS` to the project profile's `environment.allow_vars` so it reaches the
  agent.
- **No CPU or memory limits on macOS.** Unlike Tart VMs, a runaway build is not capped.
- **Shared PID namespace.** The agent runs in the host's process table; it is filesystem-
  and network-sandboxed, not process-isolated. It can see (though not necessarily signal)
  other host processes.

## Image Architecture

The Docker runtimes use a two-tier image system (the `nono` runtime builds no image):

1. **Base image** (`docker/agent-loop/<agent>/Dockerfile` in dotfiles) — shared across all
   projects. Includes the agent runtime and common tools. Re-pulled if older than 7 days.
2. **Project image** (from `.agent-loop/dependencies` or `Dockerfile.sandbox`) — per-project
   layer built on top of the base image. Cached with a content-addressed tag.

Image tags follow the pattern:
- Base: `agent-loop-sandbox-<agent>:v<hash>`
- Project: `agent-loop-sandbox-<agent>-<project>:v<hash>`

## Host-side Proxies

Ralph runs proxies on the host that the sandbox talks to: a credential-injecting proxy (not
used by the `nono` runtime, which has nono's own), a Docker API proxy, and a network
allowlist proxy. Each binds to whatever address its runtime needs — `127.0.0.1` for the
Docker and nono runtimes, the dual-stack wildcard `::` for Tart VMs, which reach the host
over a bridge.

`RALPH_PROXY_LISTEN_ADDR` overrides that address for every runtime. A proxy already running
on a different address is restarted automatically.

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
