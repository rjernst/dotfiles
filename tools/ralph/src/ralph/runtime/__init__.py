"""Runtime backend abstraction for ralph agent-loop isolation.

Provides the Runtime base class, DockerImageMixin for shared Docker image
logic, configuration loading, and a factory function to create backend
instances.
"""

import datetime
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time

from ralph.proxy import proxy_health_check
from ralph.token import read_token_from_keychain

# Directory where per-sandbox timestamp files are stored.  The mtime of each
# file records when the sandbox was last used by process_issue / ensure_sandbox.
SANDBOX_STATE_DIR = os.path.expanduser("~/.ralph/sandbox-used")


# ---------------------------------------------------------------------------
# Shared Docker image logic
# ---------------------------------------------------------------------------

class DockerImageMixin:
    """Mixin providing Docker image building logic shared by Docker runtimes.

    Expects the consuming class to set ``self.dotfiles_dir`` before calling
    any instance methods.
    """

    BASE_IMAGE_MAX_AGE_DAYS = 7

    def dockerfile_path(self, agent):
        """Path to the Dockerfile for the given agent."""
        return os.path.join(
            self.dotfiles_dir, "docker", "agent-loop", agent, "Dockerfile")

    def build_context(self, agent):
        """Path to the Docker build context directory."""
        return os.path.join(self.dotfiles_dir, "docker", "agent-loop", agent)

    @staticmethod
    def parse_base_image(dockerfile_content):
        """Extract the final-stage FROM image from Dockerfile content."""
        image = None
        for line in dockerfile_content.splitlines():
            stripped = line.strip()
            if stripped.startswith("FROM "):
                image = stripped.split()[1]
        return image

    @staticmethod
    def content_hash(dockerfile_content, base_digest):
        """Compute deterministic content hash from Dockerfile + base image digest."""
        df_hash = hashlib.sha256(dockerfile_content.encode()).hexdigest()
        bd_hash = hashlib.sha256(base_digest.encode()).hexdigest()
        combined = f"{df_hash}:{bd_hash}"
        return hashlib.sha256(combined.encode()).hexdigest()[:8]

    @staticmethod
    def image_tag(agent, chash):
        """Format the image tag from agent name and content hash."""
        return f"agent-loop-sandbox-{agent}:v{chash}"

    # Debian package names: lowercase alphanumerics, plus +, -, . (must start
    # with alnum).  We also allow ':' for architecture qualifiers (e.g.
    # "pkg:amd64") and version pinning with '='.
    _VALID_PKG_RE = re.compile(r'^[a-z0-9][a-z0-9.+\-:]+(=[a-z0-9.+\-:~]+)?$')

    @staticmethod
    def parse_dependencies(content):
        """Parse a dependencies file into a list of package names.

        Lines starting with # are comments. Inline # comments are stripped.
        Blank lines and whitespace-only lines are skipped.
        Raises ValueError if a package name doesn't match dpkg naming rules.
        """
        packages = []
        for lineno, line in enumerate(content.splitlines(), 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if not DockerImageMixin._VALID_PKG_RE.match(line):
                raise ValueError(
                    f"invalid package name on line {lineno}: {line!r}")
            packages.append(line)
        return packages

    @staticmethod
    def generate_project_dockerfile(packages):
        """Generate a Dockerfile that installs the given apt packages.

        Returns a Dockerfile string using ARG BASE_IMAGE / FROM ${BASE_IMAGE}
        that installs packages via apt-get with --no-install-recommends.
        """
        pkg_list = " ".join(f"'{p}'" for p in packages)
        return (
            "ARG BASE_IMAGE\n"
            "FROM ${BASE_IMAGE}\n"
            "USER root\n"
            "RUN apt-get update && apt-get install -y --no-install-recommends \\\n"
            f"    {pkg_list} \\\n"
            "    && rm -rf /var/lib/apt/lists/*\n"
            "USER agent\n"
        )

    @staticmethod
    def find_project_config(project_dir):
        """Check for project-level sandbox config in .agent-loop/ directory.

        Returns (type, path) where type is "dockerfile", "dependencies", or
        None if neither exists. Dockerfile.sandbox takes precedence.
        """
        agent_loop_dir = os.path.join(project_dir, ".agent-loop")
        dockerfile_path = os.path.join(agent_loop_dir, "Dockerfile.sandbox")
        if os.path.isfile(dockerfile_path):
            return ("dockerfile", dockerfile_path)
        deps_path = os.path.join(agent_loop_dir, "dependencies")
        if os.path.isfile(deps_path):
            return ("dependencies", deps_path)
        return None

    @staticmethod
    def project_image_tag(agent, project_name, base_tag, dockerfile_content):
        """Compute a project image tag from agent, project name, and content.

        Tag format: agent-loop-sandbox-{agent}-{project_name}:v{hash}
        Hash is SHA256(base_tag + ":" + dockerfile_content)[:8].
        """
        combined = f"{base_tag}:{dockerfile_content}"
        chash = hashlib.sha256(combined.encode()).hexdigest()[:8]
        return f"agent-loop-sandbox-{agent}-{project_name}:v{chash}"

    def ensure_project_image(self, agent, base_tag, project_dir,
                             force_rebuild=False):
        """Build a project-level image layer on top of the base image.

        Returns the project image tag, or base_tag if no project config exists.
        """
        if project_dir.endswith("/"):
            raise ValueError(
                f"project_dir must not end with /: {project_dir}")
        config = self.find_project_config(project_dir)
        if config is None:
            return base_tag

        config_type, config_path = config
        with open(config_path) as f:
            config_content = f.read()

        if config_type == "dependencies":
            packages = self.parse_dependencies(config_content)
            dockerfile_content = self.generate_project_dockerfile(packages)
        else:
            dockerfile_content = config_content

        project_name = os.path.basename(project_dir)
        tag = self.project_image_tag(agent, project_name, base_tag,
                                     dockerfile_content)

        if not force_rebuild and self.image_exists(tag):
            print(f"ralph: using cached project image {tag}")
            return tag

        print(f"ralph: building project image {tag}...")
        if config_type == "dockerfile":
            context_dir = os.path.join(project_dir, ".agent-loop")
            subprocess.run(
                ["docker", "build", "--build-arg", f"BASE_IMAGE={base_tag}",
                 "-t", tag, "-f", "Dockerfile.sandbox", context_dir],
                check=True,
            )
        else:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_dockerfile = os.path.join(tmp_dir, "Dockerfile")
                with open(tmp_dockerfile, "w") as f:
                    f.write(dockerfile_content)
                subprocess.run(
                    ["docker", "build", "--build-arg",
                     f"BASE_IMAGE={base_tag}", "-t", tag, tmp_dir],
                    check=True,
                )
        return tag

    @staticmethod
    def _parse_docker_timestamp(ts):
        """Parse a Docker timestamp to a timezone-aware datetime.

        Handles nanosecond precision and Z suffix.
        """
        ts = re.sub(r'(\.\d{6})\d+', r'\1', ts)
        ts = ts.replace('Z', '+00:00')
        return datetime.datetime.fromisoformat(ts)

    def get_base_digest(self, image):
        """Get the repo digest of a locally-pulled Docker image."""
        result = subprocess.run(
            ["docker", "image", "inspect", image,
             "--format", "{{index .RepoDigests 0}}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return ""

    def base_image_age_days(self, image):
        """Return the age of a local Docker image in days, or None if missing."""
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.Created}}", image],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        try:
            created = self._parse_docker_timestamp(result.stdout.strip())
            now = datetime.datetime.now(tz=datetime.timezone.utc)
            return (now - created).days
        except (ValueError, TypeError):
            return None

    def needs_rebuild(self, agent):
        """Check if the base image should be re-pulled (missing or >7 days old)."""
        with open(self.dockerfile_path(agent)) as f:
            content = f.read()
        base_image = self.parse_base_image(content)
        if not base_image:
            return True
        age = self.base_image_age_days(base_image)
        if age is None:
            return True
        return age > self.BASE_IMAGE_MAX_AGE_DAYS

    def pull_base_image(self, agent):
        """Pull the base image referenced in the agent's Dockerfile."""
        with open(self.dockerfile_path(agent)) as f:
            content = f.read()
        base_image = self.parse_base_image(content)
        if not base_image:
            raise RuntimeError(
                f"no FROM directive in {self.dockerfile_path(agent)}")
        print(f"ralph: pulling base image {base_image}...")
        subprocess.run(["docker", "pull", base_image], check=True)

    def image_exists(self, tag):
        """Check if a Docker image exists locally."""
        result = subprocess.run(
            ["docker", "image", "inspect", tag],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def compute_tag(self, agent):
        """Compute the content-addressed image tag for an agent's sandbox."""
        with open(self.dockerfile_path(agent)) as f:
            df_content = f.read()
        base_image = self.parse_base_image(df_content)
        base_digest = self.get_base_digest(base_image) if base_image else ""
        chash = self.content_hash(df_content, base_digest)
        return self.image_tag(agent, chash)

    def build_image(self, agent, tag):
        """Build the sandbox image for the given agent."""
        ctx = self.build_context(agent)
        print(f"ralph: building sandbox image {tag}...")
        subprocess.run(["docker", "build", "-t", tag, ctx], check=True)

    def ensure_image(self, agent, force_rebuild=False):
        """Ensure the sandbox image is built and up-to-date.

        Returns the image tag.
        """
        if force_rebuild or self.needs_rebuild(agent):
            self.pull_base_image(agent)

        tag = self.compute_tag(agent)

        if not force_rebuild and self.image_exists(tag):
            print(f"ralph: using cached sandbox image {tag}")
            return tag

        self.build_image(agent, tag)
        return tag


# ---------------------------------------------------------------------------
# Runtime config (.agent-loop/config.json)
# ---------------------------------------------------------------------------

def load_runtime_config(project_dir):
    """Load runtime configuration from .agent-loop/config.json.

    Returns a dict with at least {"type": "docker-sandbox"} as default.
    For "tart" type, may also include: base_image (str, required),
    cpu (int, optional), memory_gb (int, optional).

    Raises ValueError for unknown runtime types or malformed JSON.
    """
    config_path = os.path.join(project_dir, ".agent-loop", "config.json")
    if not os.path.isfile(config_path):
        return {"type": "docker-sandbox"}
    with open(config_path) as f:
        config = json.load(f)
    runtime_type = config.get("type", "docker-sandbox")
    if runtime_type not in ("docker-sandbox", "docker-container", "tart"):
        raise ValueError(
            f"ralph: unknown runtime type {runtime_type!r} "
            f"in {config_path} (expected 'docker-sandbox', "
            f"'docker-container', or 'tart')")
    config["type"] = runtime_type
    return config


def create_runtime(runtime_type, dotfiles_dir, **kwargs):
    """Factory: create the appropriate runtime backend.

    Args:
        runtime_type: "docker-sandbox", "docker-container", or "tart"
        dotfiles_dir: path to the dotfiles repository
        **kwargs: additional config (passed through from load_runtime_config)

    Returns:
        A Runtime instance.

    Raises:
        ValueError: for unknown runtime types
        NotImplementedError: for backends not yet implemented
    """
    from ralph.runtime.docker_sandbox import DockerSandboxRuntime
    from ralph.runtime.tart import TartRuntime

    if runtime_type == "docker-sandbox":
        allowed_hosts = kwargs.get("allowed_hosts")
        return DockerSandboxRuntime(dotfiles_dir, allowed_hosts=allowed_hosts)
    elif runtime_type == "docker-container":
        from ralph.runtime.container import DockerContainerRuntime
        allowed_hosts = kwargs.get("allowed_hosts")
        return DockerContainerRuntime(dotfiles_dir, allowed_hosts=allowed_hosts)
    elif runtime_type == "tart":
        # Read dependencies file if a project_dir was passed through kwargs
        config = dict(kwargs)
        project_dir = config.pop("project_dir", None)
        if project_dir and "dependencies_content" not in config:
            deps_path = os.path.join(project_dir, ".agent-loop", "dependencies")
            if os.path.isfile(deps_path):
                with open(deps_path) as f:
                    config["dependencies_content"] = f.read()
        return TartRuntime(dotfiles_dir, config=config)
    else:
        raise ValueError(f"ralph: unknown runtime type {runtime_type!r}")


# ---------------------------------------------------------------------------
# Runtime backend abstraction
# ---------------------------------------------------------------------------

class Runtime:
    """Base class defining the runtime backend interface.

    Subclasses (DockerSandboxRuntime, TartRuntime) implement these methods
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
        "- NEVER run `git init`. If git commands fail in the workspace, the "
        "sandbox runtime is misconfigured. Stop immediately and report the "
        "error — do not attempt to fix git yourself.\n"
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

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None,
                      agent="claude", api_key=None):
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

    # -- Sandbox timestamp tracking --------------------------------------------

    PRUNE_MAX_AGE_DAYS = 2

    def _touch_sandbox_timestamp(self, name):
        """Record that a sandbox was just used (create/reuse)."""
        os.makedirs(SANDBOX_STATE_DIR, exist_ok=True)
        path = os.path.join(SANDBOX_STATE_DIR, name)
        with open(path, "w"):
            pass  # mtime is all we need

    def _sandbox_last_used(self, name):
        """Return the last-used time as seconds since epoch, or None."""
        path = os.path.join(SANDBOX_STATE_DIR, name)
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _remove_sandbox_timestamp(self, name):
        """Delete the timestamp file for a removed sandbox."""
        path = os.path.join(SANDBOX_STATE_DIR, name)
        try:
            os.unlink(path)
        except OSError:
            pass

    # -- Sandbox lifecycle (abstract) -----------------------------------------

    def cleanup_sandbox(self, agent, branch):
        """Remove the sandbox for a given agent and branch."""
        raise NotImplementedError

    def prune_sandboxes(self, agent, max_age_days=None):
        """Remove orphaned sandboxes. Returns list of pruned names.

        Removes sandboxes whose workspace no longer exists AND sandboxes
        that have not been used within max_age_days (defaults to
        PRUNE_MAX_AGE_DAYS).
        """
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
