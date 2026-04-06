"""Docker container runtime backend for ralph agent-loop isolation.

Uses ``docker run`` + a filtered Docker socket proxy instead of
``docker sandbox create``.  The container runs on an ``--internal``
Docker network so it can only reach host.docker.internal (credential
proxy + socket proxy) and has no other outbound access.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

from ralph.agents import get_agent
from ralph.docker_proxy import (
    DOCKER_PROXY_PORT,
    docker_proxy_health_check,
    ensure_docker_proxy,
)
from ralph.network_proxy import (
    NETWORK_PROXY_PORT,
    ensure_network_proxy,
    network_proxy_health_check,
)
from ralph.runtime import DockerImageMixin, Runtime


NETWORK_NAME = "ralph-agent-loop"


class DockerContainerRuntime(DockerImageMixin, Runtime):
    """Manages Docker containers for agent-loop isolation.

    Unlike DockerSandboxRuntime (which uses ``docker sandbox``), this
    backend uses plain ``docker run`` with an ``--internal`` network
    for isolation and a Docker socket proxy for in-container builds.
    """

    def __init__(self, dotfiles_dir, allowed_hosts=None):
        self.dotfiles_dir = dotfiles_dir
        self.allowed_hosts = tuple(allowed_hosts) if allowed_hosts else ()
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

    # -- Network ---------------------------------------------------------------

    @staticmethod
    def _ensure_network():
        """Create the internal Docker network if it doesn't already exist.

        The ``--internal`` flag blocks all external egress while still
        allowing ``host.docker.internal`` on Docker Desktop.
        """
        result = subprocess.run(
            ["docker", "network", "create", "--internal", NETWORK_NAME],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        # Ignore "already exists" error
        if result.returncode != 0 and "already exists" not in result.stderr:
            raise RuntimeError(
                f"failed to create network {NETWORK_NAME}: {result.stderr.strip()}")

    # -- Container lifecycle ---------------------------------------------------

    def _container_exists(self, name):
        """Check if a container with the given name exists (running or stopped)."""
        result = subprocess.run(
            ["docker", "inspect", "--type", "container", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    @staticmethod
    def exec_output(sandbox_name, *cmd, workdir=None):
        """Run a command inside the container and return its stdout (stripped)."""
        base = ["docker", "exec"]
        if workdir:
            base.extend(["-w", workdir])
        base.append(sandbox_name)
        result = subprocess.run(
            base + list(cmd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def ensure_sandbox(self, agent, branch, worktree_path,
                       project_dir=None, force_rebuild=False):
        """Ensure a container exists for the given agent and branch.

        Starts the Docker socket proxy, builds images, creates an internal
        network, and launches a ``docker run -d`` container with the
        worktree and git common dir mounted.
        Returns the container name.
        """
        name = self.sandbox_name(agent, branch)
        self._worktree_path = worktree_path

        if self._container_exists(name):
            print(f"ralph: reusing container {name}")
            self._touch_sandbox_timestamp(name)
            return name

        # Ensure Docker socket proxy is running
        ensure_docker_proxy(DOCKER_PROXY_PORT, self.dotfiles_dir)

        # Ensure network proxy is running (if allowed_hosts configured)
        if self.allowed_hosts:
            ensure_network_proxy(NETWORK_PROXY_PORT, self.dotfiles_dir,
                                self.allowed_hosts)

        base_tag = self.ensure_image(agent, force_rebuild=force_rebuild)
        if project_dir:
            tag = self.ensure_project_image(agent, base_tag, project_dir,
                                            force_rebuild=force_rebuild)
        else:
            tag = base_tag

        git_common_dir = self._resolve_git_common_dir(worktree_path)

        self._ensure_network()

        print(f"ralph: creating container {name}...")
        cmd = [
            "docker", "run", "-d",
            "--name", name,
            "--network", NETWORK_NAME,
            "-v", f"{worktree_path}:{worktree_path}",
        ]
        if git_common_dir:
            cmd.extend(["-v", f"{git_common_dir}:{git_common_dir}"])
        cmd.extend([
            "-e", f"DOCKER_HOST=tcp://host.docker.internal:{DOCKER_PROXY_PORT}",
        ])
        if self.allowed_hosts:
            proxy_url = f"http://host.docker.internal:{NETWORK_PROXY_PORT}"
            cmd.extend([
                "-e", f"HTTP_PROXY={proxy_url}",
                "-e", f"HTTPS_PROXY={proxy_url}",
                "-e", "NO_PROXY=host.docker.internal",
            ])
        cmd.extend([tag, "sleep", "infinity"])
        subprocess.run(cmd, check=True)

        self._touch_sandbox_timestamp(name)
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
        """Stop and remove the container for a given agent and branch."""
        name = self.sandbox_name(agent, branch)
        print(f"ralph: removing container {name}")
        subprocess.run(
            ["docker", "stop", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            ["docker", "rm", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        self._remove_sandbox_timestamp(name)

    def remove_sandbox(self, name):
        """Remove a container by name (best-effort)."""
        subprocess.run(
            ["docker", "stop", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        subprocess.run(
            ["docker", "rm", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        self._remove_sandbox_timestamp(name)

    def prune_sandboxes(self, agent, max_age_days=None):
        """Remove orphaned or stale containers.

        Lists containers matching the agent prefix via ``docker ps -a``,
        then prunes those whose workspace no longer exists or that have
        not been used within max_age_days.

        Returns list of pruned container names.
        """
        if max_age_days is None:
            max_age_days = self.PRUNE_MAX_AGE_DAYS
        prefix = f"agent-loop-{agent}-"
        result = subprocess.run(
            ["docker", "ps", "-a",
             "--filter", f"name={prefix}",
             "--format", "{{json .}}"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            return []

        now = time.time()
        cutoff = now - max_age_days * 86400
        pruned = []

        for line in result.stdout.strip().splitlines():
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            name = entry.get("Names", "")
            if not name.startswith(prefix):
                continue

            # Check workspace via container mounts
            mounts_result = subprocess.run(
                ["docker", "inspect", "--format",
                 "{{range .Mounts}}{{.Source}} {{end}}", name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            workspace = ""
            if mounts_result.returncode == 0:
                # First mount source is the worktree path
                parts = mounts_result.stdout.strip().split()
                if parts:
                    workspace = parts[0]

            if not workspace or not os.path.exists(workspace):
                print(f"ralph: pruning orphan container {name}")
            else:
                last_used = self._sandbox_last_used(name)
                if last_used is not None and last_used >= cutoff:
                    continue
                print(f"ralph: pruning stale container {name}")

            subprocess.run(
                ["docker", "stop", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                ["docker", "rm", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            self._remove_sandbox_timestamp(name)
            pruned.append(name)

        return pruned

    # -- Git sync ---------------------------------------------------------------

    def check_in_sync(self, sandbox_name, work_dir, git):
        """Check if container can access the host worktree's git state.

        With a shared .git directory the container and host see the same
        commits, so we just verify that git resolves HEAD at the worktree
        path inside the container and it matches the host.
        """
        host_head = git.output("rev-parse", "HEAD", cwd=work_dir)
        sandbox_head = self.exec_output(
            sandbox_name, "git", "rev-parse", "HEAD", workdir=work_dir)
        return bool(host_head and sandbox_head and host_head == sandbox_head)

    def reset_to_host(self, sandbox_name, work_dir, git):
        """Reset container worktree to match host HEAD.

        With a shared .git the container already sees the same commits as
        the host.  A reset is only needed when a prior iteration left
        uncommitted changes or a detached HEAD.
        """
        host_head = git.output("rev-parse", "HEAD", cwd=work_dir)
        if not host_head:
            return False

        base = ["docker", "exec", "-w", work_dir, sandbox_name]
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
        """Verify container commits are visible on the host.

        With a shared .git directory, commits made inside the container are
        already present in the host repo — no patch extraction needed.
        This method just confirms the host can resolve head_after.
        """
        result = subprocess.run(
            ["git", "rev-parse", "--verify", head_after],
            cwd=work_dir, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0:
            print(f"ralph: error: host cannot see container commit {head_after}",
                  file=sys.stderr)
            return False
        print(f"ralph: synced commits to {work_dir}")
        return True

    # -- Iteration --------------------------------------------------------------

    def setup_git_config(self, sandbox_name, user, email):
        """Configure git user and safe directory settings inside the container."""
        subprocess.run(
            ["docker", "exec", sandbox_name,
             "git", "config", "--global", "user.name", user],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["docker", "exec", sandbox_name,
             "git", "config", "--global", "user.email", email],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["docker", "exec", sandbox_name,
             "git", "config", "--global", "--add", "safe.directory", "*"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None,
                      agent="claude", api_key=None):
        """Run a single agent iteration inside the container.

        Writes spec content to /tmp/spec.md inside the container, runs the
        agent CLI with the iteration prompt, then reads back the (possibly
        updated) spec.

        For non-proxy agents, the API key is delivered via a secret file that
        is read into an env var and deleted before the agent process starts.

        Returns (exit_code, updated_spec_content).
        """
        agent_config = get_agent(agent)
        spec_path = "/tmp/spec.md"

        # Write spec into container (-i keeps stdin open for piping)
        write_proc = subprocess.run(
            ["docker", "exec", "-i", sandbox_name,
             "tee", spec_path],
            input=spec_content, text=True, check=False,
            stdout=subprocess.DEVNULL,
        )
        if write_proc.returncode != 0:
            return write_proc.returncode, spec_content

        # For non-proxy agents, write the API key to a secret file
        secret_path = "/tmp/.agent-api-key"
        if not agent_config["uses_proxy"] and api_key:
            key_proc = subprocess.run(
                ["docker", "exec", "-i", sandbox_name,
                 "tee", secret_path],
                input=api_key, text=True, check=False,
                stdout=subprocess.DEVNULL,
            )
            if key_proc.returncode != 0:
                return key_proc.returncode, spec_content

        # Build the agent command
        cli_command = agent_config["cli_command"]
        cli_flags = agent_config["cli_flags"](model)

        if agent_config["uses_proxy"]:
            cmd = ["docker", "exec",
                   "-w", self._worktree_path]
            if env_vars:
                for k, v in env_vars.items():
                    cmd.extend(["-e", f"{k}={v}"])
            cmd.extend([
                sandbox_name, cli_command,
                "-p", self.ITERATION_PROMPT,
                "--model", model,
            ] + cli_flags)
            rc = subprocess.run(cmd, check=False).returncode
        else:
            env_var_name = agent_config["env_var_name"]
            if not re.match(r'^[A-Z_][A-Z0-9_]*$', env_var_name):
                raise ValueError(
                    f"invalid env_var_name: {env_var_name!r}")
            inner_cmd = (
                f'export {env_var_name}="$(cat {secret_path})" && '
                f"rm {secret_path} && "
                f"exec {cli_command} -p "
                + shlex.quote(self.ITERATION_PROMPT)
                + f" --model {shlex.quote(model)}"
            )
            for flag in cli_flags:
                inner_cmd += f" {shlex.quote(flag)}"
            cmd = ["docker", "exec",
                   "-w", self._worktree_path,
                   sandbox_name, "sh", "-c", inner_cmd]
            rc = subprocess.run(cmd, check=False).returncode

        # Read back (possibly updated) spec
        read_proc = subprocess.run(
            ["docker", "exec", sandbox_name, "cat", spec_path],
            stdout=subprocess.PIPE, text=True, check=False,
        )
        updated = read_proc.stdout if read_proc.returncode == 0 else spec_content

        return rc, updated

    # -- Pre-flight validation ------------------------------------------------

    def _preflight_backend_checks(self, sandbox_name):
        """Docker container pre-flight checks.

        Verifies: Docker socket proxy health, container responsiveness,
        and network isolation (outbound requests should be blocked).
        """
        failures = []

        # 1. Docker socket proxy health
        healthy, _ = docker_proxy_health_check(DOCKER_PROXY_PORT)
        if not healthy:
            failures.append(
                f"docker socket proxy not reachable at "
                f"http://localhost:{DOCKER_PROXY_PORT}/health")

        # 1b. Network proxy health (only when allowed_hosts configured)
        if self.allowed_hosts:
            healthy, _, _ = network_proxy_health_check(NETWORK_PROXY_PORT)
            if not healthy:
                failures.append(
                    f"network proxy not reachable at "
                    f"http://localhost:{NETWORK_PROXY_PORT}/health")

        # 2. Container responsiveness
        container_ok = False
        result = subprocess.run(
            ["docker", "exec", sandbox_name, "echo", "ok"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(
                f"container {sandbox_name} is not responding"
                f" — try: docker rm -f {sandbox_name}")
        else:
            container_ok = True

        # 3. Network isolation (only if container is responsive)
        if container_ok:
            # Verify curl is available before testing isolation.
            curl_check = subprocess.run(
                ["docker", "exec", sandbox_name, "which", "curl"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            if curl_check.returncode != 0:
                failures.append(
                    f"curl not found in container {sandbox_name}"
                    " — cannot verify network isolation")
            else:
                result = subprocess.run(
                    ["docker", "exec", sandbox_name,
                     "curl", "-s", "--max-time", "3",
                     "https://google.com"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, check=False,
                )
                if result.returncode == 0:
                    failures.append(
                        f"network isolation not working for container "
                        f"{sandbox_name}"
                        " — outbound requests should be blocked")

        return failures
