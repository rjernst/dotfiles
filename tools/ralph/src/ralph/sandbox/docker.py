"""Docker sandbox backend for ralph agent-loop isolation."""

import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from ralph.sandbox import SandboxBackend


class DockerSandbox(SandboxBackend):
    """Manages Docker sandbox images for agent-loop isolation."""

    BASE_IMAGE_MAX_AGE_DAYS = 7

    def __init__(self, dotfiles_dir):
        self.dotfiles_dir = dotfiles_dir
        self._worktree_path = None

    def proxy_host(self):
        """Return the hostname for reaching the credential proxy."""
        return "host.docker.internal"

    def check_prerequisites(self):
        """Check that Docker is available. Returns list of error messages."""
        errors = []
        if not shutil.which("docker"):
            errors.append("docker is not installed")
        return errors

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
            if not DockerSandbox._VALID_PKG_RE.match(line):
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

    # -- Sandbox lifecycle --------------------------------------------------

    @staticmethod
    def _docker_sandbox_ls():
        """List sandboxes via 'docker sandbox ls --json'. Returns parsed JSON."""
        result = subprocess.run(
            ["docker", "sandbox", "ls", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            return {"vms": []}
        try:
            return json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return {"vms": []}

    def sandbox_exists(self, name):
        """Check if a sandbox with the given name exists."""
        data = self._docker_sandbox_ls()
        for vm in data.get("vms", []):
            if vm.get("name") == name:
                return True
        return False

    @staticmethod
    def _docker_sandbox_create(name, tag, worktree_path, git_common_dir=None):
        """Create a new Docker sandbox.

        Passes worktree_path as the primary workspace.  When git_common_dir is
        provided (the repo's shared .git directory), it is added as a second
        workspace so that the worktree's .git pointer resolves inside the
        sandbox.
        """
        workspaces = [worktree_path]
        if git_common_dir:
            workspaces.append(git_common_dir)
        subprocess.run(
            ["docker", "sandbox", "create",
             "--name", name, "-t", tag, "claude"] + workspaces,
            check=True,
        )

    @staticmethod
    def exec_output(sandbox_name, *cmd, workdir=None):
        """Run a command inside the sandbox and return its stdout (stripped)."""
        base = ["docker", "sandbox", "exec"]
        if workdir:
            base.extend(["-w", workdir])
        base.append(sandbox_name)
        result = subprocess.run(
            base + list(cmd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def check_in_sync(self, sandbox_name, work_dir, git):
        """Check if sandbox can access the host worktree's git state.

        With a shared .git directory the sandbox and host see the same
        commits, so we just verify that git resolves HEAD at the worktree
        path inside the sandbox and it matches the host.
        """
        host_head = git.output("rev-parse", "HEAD", cwd=work_dir)
        sandbox_head = self.exec_output(
            sandbox_name, "git", "rev-parse", "HEAD", workdir=work_dir)
        return bool(host_head and sandbox_head and host_head == sandbox_head)

    def reset_to_host(self, sandbox_name, work_dir, git):
        """Reset sandbox worktree to match host HEAD.

        With a shared .git the sandbox already sees the same commits as
        the host.  A reset is only needed when a prior iteration left
        uncommitted changes or a detached HEAD.
        """
        host_head = git.output("rev-parse", "HEAD", cwd=work_dir)
        if not host_head:
            return False

        base = ["docker", "sandbox", "exec", "-w", work_dir, sandbox_name]
        rc = subprocess.run(
            base + ["git", "reset", "--hard", host_head],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode
        if rc != 0:
            return False
        subprocess.run(
            base + ["git", "clean", "-fd"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True

    def sync_to_host(self, sandbox_name, head_before, head_after, work_dir):
        """Verify sandbox commits are visible on the host.

        With a shared .git directory, commits made inside the sandbox are
        already present in the host repo — no patch extraction needed.
        This method just confirms the host can resolve head_after.
        """
        result = subprocess.run(
            ["git", "rev-parse", "--verify", head_after],
            cwd=work_dir, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            print(f"ralph: error: host cannot see sandbox commit {head_after}",
                  file=sys.stderr)
            return False
        print(f"ralph: synced commits to {work_dir}")
        return True

    @staticmethod
    def apply_network_policy(name):
        """Apply deny-by-default network policy with allowed hosts."""
        subprocess.run(
            ["docker", "sandbox", "network", "proxy", name,
             "--policy", "deny",
             "--allow-host", "localhost",
             "--allow-host", "api.anthropic.com",
             "--allow-host", "statsig.anthropic.com",
             "--allow-host", "sentry.io"],
            check=True,
        )

    def ensure_sandbox(self, agent, branch, worktree_path,
                       project_dir=None, force_rebuild=False):
        """Ensure a sandbox exists for the given agent and branch.

        Reuses an existing sandbox or creates a new one with network policy.
        If project_dir is provided, builds a project-level image layer.
        The host repo's shared .git directory is mounted as a second
        workspace so the worktree's .git pointer resolves inside the sandbox.
        Returns the sandbox name.
        """
        name = self.sandbox_name(agent, branch)
        self._worktree_path = worktree_path
        if self.sandbox_exists(name):
            print(f"ralph: reusing sandbox {name}")
            return name
        base_tag = self.ensure_image(agent, force_rebuild=force_rebuild)
        if project_dir:
            tag = self.ensure_project_image(agent, base_tag, project_dir,
                                            force_rebuild=force_rebuild)
        else:
            tag = base_tag
        git_common_dir = self._resolve_git_common_dir(worktree_path)
        print(f"ralph: creating sandbox {name}...")
        self._docker_sandbox_create(name, tag, worktree_path, git_common_dir)
        self.apply_network_policy(name)
        return name

    @staticmethod
    def _resolve_git_common_dir(worktree_path):
        """Resolve the shared .git directory for a worktree (or regular repo).

        Returns the absolute path to the repo's .git directory, or None
        if git rev-parse fails (e.g. not a git repo).
        """
        result = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=worktree_path, capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        return os.path.realpath(os.path.join(worktree_path, raw))

    def cleanup_sandbox(self, agent, branch):
        """Remove the sandbox for a given agent and branch."""
        name = self.sandbox_name(agent, branch)
        print(f"ralph: removing sandbox {name}")
        subprocess.run(
            ["docker", "sandbox", "rm", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    def prune_sandboxes(self, agent):
        """Remove orphaned sandboxes whose workspace paths no longer exist.

        Returns list of pruned sandbox names.
        """
        prefix = f"agent-loop-{agent}-"
        data = self._docker_sandbox_ls()
        pruned = []
        for vm in data.get("vms", []):
            name = vm.get("name", "")
            if not name.startswith(prefix):
                continue
            workspace = vm.get("workspace", "")
            if workspace and os.path.exists(workspace):
                continue
            print(f"ralph: pruning orphan sandbox {name}")
            subprocess.run(
                ["docker", "sandbox", "rm", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            pruned.append(name)
        return pruned

    # -- Iteration ------------------------------------------------------------

    def setup_git_config(self, sandbox_name, user, email):
        """Configure git user and safe directory settings inside the sandbox."""
        subprocess.run(
            ["docker", "sandbox", "exec", sandbox_name,
             "git", "config", "--global", "user.name", user],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["docker", "sandbox", "exec", sandbox_name,
             "git", "config", "--global", "user.email", email],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["docker", "sandbox", "exec", sandbox_name,
             "git", "config", "--global", "--add", "safe.directory", "*"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None):
        """Run a single Claude Code iteration inside the sandbox.

        Writes spec content to /tmp/spec.md inside the sandbox, runs claude
        with the iteration prompt, then reads back the (possibly updated) spec.

        Returns (exit_code, updated_spec_content).
        """
        spec_path = "/tmp/spec.md"

        # Write spec into sandbox (-i keeps stdin open for piping)
        write_proc = subprocess.run(
            ["docker", "sandbox", "exec", "-i", sandbox_name,
             "tee", spec_path],
            input=spec_content, text=True, check=False,
            stdout=subprocess.DEVNULL,
        )
        if write_proc.returncode != 0:
            return write_proc.returncode, spec_content

        # Run claude with iteration prompt
        cmd = ["docker", "sandbox", "exec",
               "-w", self._worktree_path]
        if env_vars:
            for k, v in env_vars.items():
                cmd.extend(["-e", f"{k}={v}"])
        cmd.extend([
            sandbox_name, "claude",
            "-p", self.ITERATION_PROMPT,
            "--model", model,
            "--dangerously-skip-permissions",
            "--effort", "high",
        ])
        rc = subprocess.run(cmd, check=False).returncode

        # Read back (possibly updated) spec
        read_proc = subprocess.run(
            ["docker", "sandbox", "exec", sandbox_name, "cat", spec_path],
            stdout=subprocess.PIPE, text=True, check=False,
        )
        updated = read_proc.stdout if read_proc.returncode == 0 else spec_content

        return rc, updated

    # -- Selftest --------------------------------------------------------------

    def remove_sandbox(self, name):
        """Remove a sandbox by name (best-effort)."""
        subprocess.run(
            ["docker", "sandbox", "rm", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    # -- Pre-flight validation ------------------------------------------------

    def _preflight_backend_checks(self, sandbox_name):
        """Docker-specific pre-flight checks: sandbox responsiveness and network policy."""
        failures = []
        sandbox_ok = False
        result = subprocess.run(
            ["docker", "sandbox", "exec", sandbox_name, "echo", "ok"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(
                f"sandbox {sandbox_name} is not responding"
                f" — try: docker sandbox rm {sandbox_name}")
        else:
            sandbox_ok = True

        # Network policy applied (only if sandbox is responsive)
        if sandbox_ok:
            result = subprocess.run(
                ["docker", "sandbox", "exec", sandbox_name,
                 "curl", "-s", "--max-time", "3", "https://google.com"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            if result.returncode == 0:
                failures.append(
                    f"network policy not applied to sandbox {sandbox_name}"
                    " — outbound requests should be blocked")

        return failures
