"""Docker sandbox runtime backend for ralph agent-loop isolation."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from ralph.agents import get_agent
from ralph.runtime import DockerImageMixin, Runtime


class DockerSandboxRuntime(DockerImageMixin, Runtime):
    """Manages Docker sandbox images for agent-loop isolation."""

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
    def _docker_sandbox_create(name, tag, worktree_path, git_common_dir=None,
                               sandbox_agent="claude"):
        """Create a new Docker sandbox.

        Passes worktree_path as the primary workspace.  When git_common_dir is
        provided (the repo's shared .git directory), it is added as a second
        workspace so that the worktree's .git pointer resolves inside the
        sandbox.

        sandbox_agent is the docker sandbox subcommand (e.g. "claude" or
        "shell") — looked up from the agent config's sandbox_agent field.
        """
        workspaces = [worktree_path]
        if git_common_dir:
            workspaces.append(git_common_dir)
        subprocess.run(
            ["docker", "sandbox", "create",
             "--name", name, "-t", tag, sandbox_agent] + workspaces,
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

    def apply_network_policy(self, name, allowed_hosts):
        """Apply deny-by-default network policy with allowed hosts.

        allowed_hosts is the list of hosts from the agent config's
        allowed_hosts field.  Project-level hosts from the constructor
        are appended.  "localhost" is always included.
        """
        cmd = ["docker", "sandbox", "network", "proxy", name,
               "--policy", "deny",
               "--allow-host", "localhost"]
        for host in list(allowed_hosts) + list(self.allowed_hosts):
            cmd.extend(["--allow-host", host])
        subprocess.run(cmd, check=True)

    @staticmethod
    def _config_fingerprint(tag, allowed_hosts, worktree_path, git_common_dir):
        """Compute a fingerprint of the sandbox configuration.

        Captures everything that goes into sandbox creation so that config
        changes (image, network policy, workspace paths) trigger a recreate.
        """
        config = json.dumps({
            "tag": tag,
            "allowed_hosts": sorted(set(allowed_hosts)),
            "worktree_path": worktree_path,
            "git_common_dir": git_common_dir,
        }, sort_keys=True)
        return hashlib.sha256(config.encode()).hexdigest()[:16]

    def _read_sandbox_fingerprint(self, name):
        """Read the stored config fingerprint from inside the sandbox."""
        return self.exec_output(name, "cat", "/tmp/.sandbox-config")

    def _write_sandbox_fingerprint(self, name, fingerprint):
        """Write the config fingerprint inside the sandbox."""
        subprocess.run(
            ["docker", "sandbox", "exec", "-i", name,
             "tee", "/tmp/.sandbox-config"],
            input=fingerprint, text=True, check=False,
            stdout=subprocess.DEVNULL,
        )

    def ensure_sandbox(self, agent, branch, worktree_path,
                       project_dir=None, force_rebuild=False):
        """Ensure a sandbox exists for the given agent and branch.

        Reuses an existing sandbox if its config fingerprint matches.
        Otherwise removes the stale sandbox and creates a fresh one.
        If project_dir is provided, builds a project-level image layer.
        The host repo's shared .git directory is mounted as a second
        workspace so the worktree's .git pointer resolves inside the sandbox.
        Returns the sandbox name.
        """
        agent_config = get_agent(agent)
        name = self.sandbox_name(agent, branch)
        self._worktree_path = worktree_path

        base_tag = self.ensure_image(agent, force_rebuild=force_rebuild)
        if project_dir:
            tag = self.ensure_project_image(agent, base_tag, project_dir,
                                            force_rebuild=force_rebuild)
        else:
            tag = base_tag

        all_hosts = list(agent_config["allowed_hosts"]) + list(self.allowed_hosts)
        git_common_dir = self._resolve_git_common_dir(worktree_path)
        fingerprint = self._config_fingerprint(tag, all_hosts, worktree_path,
                                               git_common_dir)

        if self.sandbox_exists(name):
            stored = self._read_sandbox_fingerprint(name)
            if stored == fingerprint:
                print(f"ralph: reusing sandbox {name}")
                return name
            print(f"ralph: config changed, recreating sandbox {name}")
            self.remove_sandbox(name)

        print(f"ralph: creating sandbox {name}...")
        self._docker_sandbox_create(name, tag, worktree_path, git_common_dir,
                                    sandbox_agent=agent_config["sandbox_agent"])
        self.apply_network_policy(name, agent_config["allowed_hosts"])
        self._touch_sandbox_timestamp(name)
        self._write_sandbox_fingerprint(name, fingerprint)
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
        self._remove_sandbox_timestamp(name)

    def prune_sandboxes(self, agent, max_age_days=None):
        """Remove orphaned or stale sandboxes.

        A sandbox is pruned if its workspace path no longer exists OR if it
        has not been used within max_age_days.  Sandboxes with no recorded
        timestamp are treated as stale (they predate timestamp tracking).

        Returns list of pruned sandbox names.
        """
        if max_age_days is None:
            max_age_days = self.PRUNE_MAX_AGE_DAYS
        prefix = f"agent-loop-{agent}-"
        data = self._docker_sandbox_ls()
        now = time.time()
        cutoff = now - max_age_days * 86400
        pruned = []
        for vm in data.get("vms", []):
            name = vm.get("name", "")
            if not name.startswith(prefix):
                continue
            # Workspace gone — always prune
            workspace = vm.get("workspace", "")
            if not workspace or not os.path.exists(workspace):
                print(f"ralph: pruning orphan sandbox {name}")
            else:
                # Workspace exists — prune only if stale
                last_used = self._sandbox_last_used(name)
                if last_used is not None and last_used >= cutoff:
                    continue
                print(f"ralph: pruning stale sandbox {name}")
            subprocess.run(
                ["docker", "sandbox", "rm", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            self._remove_sandbox_timestamp(name)
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

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None,
                      agent="claude", api_key=None):
        """Run a single agent iteration inside the sandbox.

        Writes spec content to /tmp/spec.md inside the sandbox, runs the
        agent CLI with the iteration prompt, then reads back the (possibly
        updated) spec.

        For cursor agent, the API key is delivered via a secret file that
        is read into an env var and deleted before the agent process starts.

        Returns (exit_code, updated_spec_content).
        """
        agent_config = get_agent(agent)
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

        # For non-proxy agents, write the API key to a secret file
        secret_path = "/tmp/.agent-api-key"
        if not agent_config["uses_proxy"] and api_key:
            key_proc = subprocess.run(
                ["docker", "sandbox", "exec", "-i", sandbox_name,
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
            # Direct exec: docker sandbox exec ... <cli_command> -p <prompt> --model <model> <flags>
            cmd = ["docker", "sandbox", "exec",
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
            # Secret file lifecycle: read key into env var, delete file,
            # then exec the agent (exec replaces the shell, so the key
            # exists only in the agent process's environment).
            env_var_name = agent_config["env_var_name"]
            inner_cmd = (
                f'export {env_var_name}="$(cat {secret_path})" && '
                f"rm {secret_path} && "
                f"exec {cli_command} -p "
                + shlex.quote(self.ITERATION_PROMPT)
                + f" --model {shlex.quote(model)}"
            )
            for flag in cli_flags:
                inner_cmd += f" {shlex.quote(flag)}"
            cmd = ["docker", "sandbox", "exec",
                   "-w", self._worktree_path,
                   sandbox_name, "sh", "-c", inner_cmd]
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
        self._remove_sandbox_timestamp(name)

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
