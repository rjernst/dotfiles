"""Sandbox backend abstraction for ralph agent-loop isolation.

Provides the SandboxBackend base class, configuration loading, and a factory
function to create backend instances.
"""

import json
import os
import re
import time

from ralph.proxy import proxy_health_check
from ralph.token import read_token_from_keychain


# ---------------------------------------------------------------------------
# Sandbox config (.agent-loop/config.json)
# ---------------------------------------------------------------------------

def load_sandbox_config(project_dir):
    """Load sandbox configuration from .agent-loop/config.json.

    Returns a dict with at least {"type": "docker"} as default.
    For "tart" type, may also include: base_image (str, required),
    cpu (int, optional), memory_gb (int, optional).

    Raises ValueError for unknown sandbox types or malformed JSON.
    """
    config_path = os.path.join(project_dir, ".agent-loop", "config.json")
    if not os.path.isfile(config_path):
        return {"type": "docker"}
    with open(config_path) as f:
        config = json.load(f)
    sandbox_type = config.get("type", "docker")
    if sandbox_type not in ("docker", "tart"):
        raise ValueError(
            f"ralph: unknown sandbox type {sandbox_type!r} "
            f"in {config_path} (expected 'docker' or 'tart')")
    config["type"] = sandbox_type
    return config


def create_sandbox_backend(sandbox_type, dotfiles_dir, **kwargs):
    """Factory: create the appropriate sandbox backend.

    Args:
        sandbox_type: "docker" or "tart"
        dotfiles_dir: path to the dotfiles repository
        **kwargs: additional config (passed through from load_sandbox_config)

    Returns:
        A SandboxBackend instance.

    Raises:
        ValueError: for unknown sandbox types
        NotImplementedError: for backends not yet implemented
    """
    from ralph.sandbox.docker import DockerSandbox
    from ralph.sandbox.tart import TartSandbox

    if sandbox_type == "docker":
        return DockerSandbox(dotfiles_dir)
    elif sandbox_type == "tart":
        # Read dependencies file if a project_dir was passed through kwargs
        config = dict(kwargs)
        project_dir = config.pop("project_dir", None)
        if project_dir and "dependencies_content" not in config:
            deps_path = os.path.join(project_dir, ".agent-loop", "dependencies")
            if os.path.isfile(deps_path):
                with open(deps_path) as f:
                    config["dependencies_content"] = f.read()
        return TartSandbox(dotfiles_dir, config=config)
    else:
        raise ValueError(f"ralph: unknown sandbox type {sandbox_type!r}")


# ---------------------------------------------------------------------------
# Sandbox backend abstraction
# ---------------------------------------------------------------------------

class SandboxBackend:
    """Base class defining the sandbox backend interface.

    Subclasses (DockerSandbox, TartSandbox) implement these methods
    for their respective isolation technologies.
    """

    ITERATION_PROMPT = (
        "You are an AI coding agent. You will be invoked repeatedly "
        "— once per task.\n"
        "Read the spec file at `/tmp/spec.md` for what to build.\n"
        "\n"
        "Your job this iteration: implement EXACTLY ONE task, then stop.\n"
        "\n"
        "Steps:\n"
        "1. Study the spec and existing codebase (especially CLAUDE.md) "
        "to understand patterns\n"
        "2. Check git log to see what has already been implemented\n"
        "3. Pick the FIRST incomplete task from the spec\n"
        "4. Implement that single task fully — no stubs or placeholders\n"
        "\n"
        "If ALL tasks are already complete, just say so "
        "— do not make any commits.\n"
        "\n"
        "Rules:\n"
        "- Follow conventions in CLAUDE.md if it exists\n"
        "- Search the codebase before assuming something isn't implemented\n"
        "\n"
        "For each task, follow this workflow:\n"
        "\n"
        "1. **Implement** — Write the code described in the task\n"
        "2. **Test** — Write tests that cover the task's Acceptance "
        "criteria\n"
        "3. **Verify** — Run tests and any commands listed in Acceptance. "
        "Fix failures until all pass.\n"
        "4. **Self-review** — Review your changes from a fresh perspective, "
        "as if you are a different developer seeing this code for the "
        "first time. Look at the full diff of your changes and check "
        "for:\n"
        "   - Bugs, off-by-one errors, edge cases\n"
        "   - Logic errors or missed requirements from the spec\n"
        "   - Adherence to project conventions (CLAUDE.md, existing "
        "patterns)\n"
        "   - Security issues, resource leaks\n"
        "   If using Claude Code, use the Agent tool to spawn a "
        "\"feature-dev:code-reviewer\" subagent for this step — a fresh "
        "context catches things you will miss.\n"
        "   Fix any issues found, re-run tests, and re-review if changes "
        "were substantial.\n"
        "5. **Commit** — Stage and commit your changes with a clear "
        "message\n"
        "6. **Update spec** — Mark the step `[done]` and record any "
        "decisions or deviations\n"
        "\n"
        "IMPORTANT: Do NOT implement more than one task. One task, one "
        "commit, then stop. The loop will call you again for the next "
        "task.\n"
        "\n"
        "Spec maintenance rules:\n"
        "- Mark each step `[done]` when complete.\n"
        "- Record design decisions that emerged during implementation "
        "as notes under the step.\n"
        "- Minor deviations (e.g. flag name changes, reordered logic) "
        "should be noted and the spec updated to match.\n"
        "- Significant design changes (e.g. new subcommands, changed "
        "architecture, removed features) require pausing for user review "
        "before proceeding.\n"
        "\n"
        "Unfulfillable tasks:\n"
        "- If a task cannot be completed because required tools or "
        "infrastructure are unavailable (e.g., test runner not installed, "
        "build tool missing, external service unreachable), append "
        "`[blocked: <reason>]` to the step heading line "
        "(e.g., `### Step 3: Run tests [blocked: pytest not installed]`) "
        "and do NOT commit. The outer loop will detect this marker and "
        "transition the issue to `status:needs-attention`.\n"
        "\n"
        "Run all checks:\n"
        "- The 'Run all checks' step (typically the final step) must "
        "ALWAYS execute the full test suite, linter, and syntax checks "
        "— even if earlier steps already ran individual tests. This step "
        "catches cross-cutting regressions. Never skip it or mark it done "
        "without actually running the checks."
    )

    @staticmethod
    def sandbox_name(agent, branch):
        """Generate sandbox name from agent and branch.

        Sanitizes the branch: replaces '/' with '-', strips leading/trailing
        hyphens, collapses consecutive hyphens, and lowercases.
        """
        sanitized = branch.replace("/", "-").lower()
        sanitized = re.sub(r'-+', '-', sanitized).strip("-")
        return f"agent-loop-{agent}-{sanitized}"

    def proxy_host(self):
        """Return the hostname for reaching the credential proxy."""
        raise NotImplementedError

    def ensure_image(self, agent, force_rebuild=False):
        """Ensure the sandbox image is built and up-to-date. Returns image tag."""
        raise NotImplementedError

    def ensure_sandbox(self, agent, branch, worktree_path, **kwargs):
        """Ensure a sandbox exists for the given agent and branch. Returns name."""
        raise NotImplementedError

    def setup_git_config(self, sandbox_name, user, email):
        """Configure git user and safe directory settings inside the sandbox."""
        raise NotImplementedError

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None):
        """Run a single iteration inside the sandbox. Returns (exit_code, updated_spec)."""
        raise NotImplementedError

    def preflight_check(self, sandbox_name, agent, proxy_port):
        """Run pre-flight validation before starting an iteration.

        Checks token validity, proxy health, and backend-specific checks.
        Returns a list of failure messages. Empty list means all checks passed.
        """
        failures = []

        # 1. Token valid
        data = read_token_from_keychain(agent)
        if data is None:
            failures.append(
                f"no token found for agent {agent}"
                " — run: ralph store-token")
        else:
            now_ms = int(time.time() * 1000)
            expires_at = data.get("expiresAt", 0)
            if expires_at <= now_ms:
                failures.append(
                    f"token expired for agent {agent}"
                    " — run: ralph store-token")

        # 2. Proxy running
        healthy, _ = proxy_health_check(proxy_port)
        if not healthy:
            failures.append(
                f"proxy not reachable at http://localhost:{proxy_port}/health"
                " — start the credential proxy")

        # 3. Backend-specific checks
        failures.extend(
            self._preflight_backend_checks(sandbox_name))

        return failures

    def _preflight_backend_checks(self, sandbox_name):
        """Backend-specific pre-flight checks. Override in subclasses.

        Returns a list of failure messages.
        """
        raise NotImplementedError

    def cleanup_sandbox(self, agent, branch):
        """Remove the sandbox for a given agent and branch."""
        raise NotImplementedError

    def prune_sandboxes(self, agent):
        """Remove orphaned sandboxes. Returns list of pruned names."""
        raise NotImplementedError

    def remove_sandbox(self, name):
        """Remove a sandbox by name (best-effort)."""
        raise NotImplementedError

    def check_prerequisites(self):
        """Check that required tools are available. Returns list of error messages."""
        raise NotImplementedError

    def check_in_sync(self, sandbox_name, work_dir, git):
        """Check if sandbox content matches host worktree HEAD."""
        raise NotImplementedError

    def reset_to_host(self, sandbox_name, work_dir, git):
        """Reset sandbox git state to match host worktree."""
        raise NotImplementedError

    def sync_to_host(self, sandbox_name, head_before, head_after, work_dir):
        """Sync commits from sandbox to host worktree."""
        raise NotImplementedError
