"""Docker sandbox runtime backend for ralph agent-loop isolation.

Uses ``sbx`` (Docker Sandboxes CLI) for microVM isolation.
"""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from ralph.agents import get_agent
from ralph.runtime import DockerImageMixin, Runtime


class DockerSandboxRuntime(DockerImageMixin, Runtime):
    """Manages Docker sandbox images for agent-loop isolation."""

    def __init__(self, dotfiles_dir, allowed_hosts=None):
        self.dotfiles_dir = dotfiles_dir
        self.allowed_hosts = tuple(allowed_hosts) if allowed_hosts else ()
        self._worktree_path = None

    # Docker Desktop routes host.docker.internal to the host's loopback,
    # so the proxies never need to be exposed beyond 127.0.0.1.
    PROXY_LISTEN_ADDR = "127.0.0.1"

    def proxy_host(self):
        """Return the hostname for reaching the credential proxy."""
        return "host.docker.internal"

    @classmethod
    def _max_sandbox_name_length(cls):
        """Upper bound on sandbox names.

        sbx uses a single daemon socket (not per-sandbox sockets), so there
        is no sandbox-name-driven path length constraint.
        """
        return None

    def check_prerequisites(self):
        """Check that Docker and sbx are available. Returns list of error messages."""
        errors = []
        if not shutil.which("docker"):
            errors.append("docker is not installed")
        if not shutil.which("sbx"):
            errors.append(
                "sbx is not installed — install with: "
                "brew trust docker/tap && brew install docker/tap/sbx")
        return errors

    # -- Sandbox lifecycle --------------------------------------------------

    @staticmethod
    def _docker_sandbox_ls():
        """List sandboxes via 'sbx ls --json'. Returns parsed JSON."""
        result = subprocess.run(
            ["sbx", "ls", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            return {"sandboxes": []}
        try:
            return json.loads(result.stdout)
        except (json.JSONDecodeError, ValueError):
            return {"sandboxes": []}

    def sandbox_exists(self, name):
        """Check if a sandbox with the given name exists."""
        data = self._docker_sandbox_ls()
        for vm in data.get("sandboxes", []):
            if vm.get("name") == name:
                return True
        return False

    @staticmethod
    def _docker_sandbox_create(name, tag, worktree_path, git_common_dir=None,
                               sandbox_agent="claude"):
        """Create a new Docker sandbox via sbx.

        Passes worktree_path as the primary workspace.  When git_common_dir is
        provided (the repo's shared .git directory), it is added as a second
        workspace so that the worktree's .git pointer resolves inside the
        sandbox.

        sandbox_agent is the sbx agent subcommand (e.g. "claude" or "shell").
        """
        workspaces = [worktree_path]
        if git_common_dir:
            workspaces.append(git_common_dir)
        subprocess.run(
            ["sbx", "create",
             "--name", name, "-t", tag, sandbox_agent] + workspaces,
            check=True,
        )

    @staticmethod
    def exec_output(sandbox_name, *cmd, workdir=None):
        """Run a command inside the sandbox and return its stdout (stripped)."""
        base = ["sbx", "exec"]
        if workdir:
            base.extend(["-w", workdir])
        base.append(sandbox_name)
        result = subprocess.run(
            base + list(cmd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def check_in_sync(self, sandbox_name, work_dir, git):
        """Check if sandbox can access the host worktree's git state."""
        host_head = git.output("rev-parse", "HEAD", cwd=work_dir)
        sandbox_head = self.exec_output(
            sandbox_name, "git", "rev-parse", "HEAD", workdir=work_dir)
        return bool(host_head and sandbox_head and host_head == sandbox_head)

    def reset_to_host(self, sandbox_name, work_dir, git):
        """Reset sandbox worktree to match host HEAD."""
        host_head = git.output("rev-parse", "HEAD", cwd=work_dir)
        if not host_head:
            return False

        base = ["sbx", "exec", "-w", work_dir, sandbox_name]
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
    def _ensure_global_policy():
        """Initialize the global sbx network policy to deny-all if not set.

        Idempotent: silently ignores the 'already initialized' error.
        """
        subprocess.run(
            ["sbx", "policy", "init", "deny-all"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _sbx_template_ls():
        """List sbx templates. Returns list of (repository, tag) tuples."""
        result = subprocess.run(
            ["sbx", "template", "ls"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        templates = []
        if result.returncode != 0:
            return templates
        lines = result.stdout.strip().splitlines()
        for line in lines[1:]:  # skip header
            parts = line.split()
            if len(parts) >= 2:
                templates.append((parts[0], parts[1]))
        return templates

    def _sbx_template_loaded(self, tag):
        """Check if a Docker image tag is already loaded in the sbx template store."""
        if ":" in tag:
            name, version = tag.rsplit(":", 1)
        else:
            name, version = tag, "latest"
        for repo, tmpl_tag in self._sbx_template_ls():
            # sbx prefixes with docker.io/library/ for unqualified image names
            if name in repo and tmpl_tag == version:
                return True
        return False

    def _ensure_template_loaded(self, tag):
        """Ensure a locally-built Docker image is loaded into the sbx template store.

        sbx uses microVMs and cannot access Docker's local image store directly.
        This exports the image to a tar and loads it into sbx's template store.
        Skips if the tag is already present.
        """
        if self._sbx_template_loaded(tag):
            return
        print(f"ralph: loading template {tag} into sbx...")
        with tempfile.TemporaryDirectory() as tmp_dir:
            tar_path = os.path.join(tmp_dir, "image.tar")
            subprocess.run(
                ["docker", "save", tag, "-o", tar_path],
                check=True, stdout=subprocess.DEVNULL,
            )
            subprocess.run(["sbx", "template", "load", tar_path], check=True)

    def apply_network_policy(self, name, allowed_hosts):
        """Apply network policy: deny-all globally, then allow specific hosts per sandbox.

        allowed_hosts is the list of hosts from the agent config's
        allowed_hosts field.  Project-level hosts from the constructor
        are appended.  host.docker.internal and localhost are always
        included so the credential proxy remains reachable.

        Both are required: host.docker.internal for direct connections,
        and localhost because the sbx gateway resolves host.docker.internal
        to 127.0.0.1 (the host) when checking proxy-forwarded requests.
        """
        all_hosts = (["host.docker.internal", "localhost"]
                     + list(allowed_hosts)
                     + list(self.allowed_hosts))
        hosts_str = ",".join(all_hosts)
        subprocess.run(
            ["sbx", "policy", "allow", "network", "--sandbox", name, hosts_str],
            check=True,
        )

    @staticmethod
    def _config_fingerprint(tag, allowed_hosts, worktree_path, git_common_dir):
        """Compute a fingerprint of the sandbox configuration."""
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
            ["sbx", "exec", "-i", name,
             "tee", "/tmp/.sandbox-config"],
            input=fingerprint, text=True, check=False,
            stdout=subprocess.DEVNULL,
        )

    def ensure_sandbox(self, agent, branch, worktree_path,
                       project_dir=None, force_rebuild=False):
        """Ensure a sandbox exists for the given agent and branch.

        Reuses an existing sandbox if its config fingerprint matches.
        Otherwise removes the stale sandbox and creates a fresh one.
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
                self._touch_sandbox_timestamp(name)
                return name
            print(f"ralph: config changed, recreating sandbox {name}")
            self.remove_sandbox(name)

        self._ensure_global_policy()
        self._ensure_template_loaded(tag)

        print(f"ralph: creating sandbox {name}...")
        self._docker_sandbox_create(name, tag, worktree_path, git_common_dir,
                                    sandbox_agent=agent_config["sandbox_agent"])
        self.apply_network_policy(name, agent_config["allowed_hosts"])
        self._touch_sandbox_timestamp(name)
        self._write_sandbox_fingerprint(name, fingerprint)
        return name

    @staticmethod
    def _resolve_git_common_dir(worktree_path):
        """Resolve the shared .git directory for a worktree (or regular repo)."""
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
            ["sbx", "rm", "--force", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        self._remove_sandbox_timestamp(name)

    def prune_sandboxes(self, agent, max_age_days=None):
        """Remove orphaned or stale sandboxes.

        A sandbox is pruned if its workspace path no longer exists OR if it
        has not been used within max_age_days.  Sandboxes with no recorded
        timestamp are treated as stale.

        Returns list of pruned sandbox names.
        """
        if max_age_days is None:
            max_age_days = self.PRUNE_MAX_AGE_DAYS
        prefix = f"agent-loop-{agent}-"
        data = self._docker_sandbox_ls()
        now = time.time()
        cutoff = now - max_age_days * 86400
        pruned = []
        for vm in data.get("sandboxes", []):
            name = vm.get("name", "")
            if not name.startswith(prefix):
                continue
            workspaces = vm.get("workspaces", [])
            workspace = workspaces[0] if workspaces else ""
            if not workspace or not os.path.exists(workspace):
                print(f"ralph: pruning orphan sandbox {name}")
            else:
                last_used = self._sandbox_last_used(name)
                if last_used is not None and last_used >= cutoff:
                    continue
                print(f"ralph: pruning stale sandbox {name}")
            subprocess.run(
                ["sbx", "rm", "--force", name],
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
            ["sbx", "exec", sandbox_name,
             "git", "config", "--global", "user.name", user],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["sbx", "exec", sandbox_name,
             "git", "config", "--global", "user.email", email],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["sbx", "exec", sandbox_name,
             "git", "config", "--global", "--add", "safe.directory", "*"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None,
                      agent="claude", api_key=None):
        """Run a single agent iteration inside the sandbox.

        Writes spec content to /tmp/spec.md inside the sandbox, runs the
        agent CLI with the iteration prompt, then reads back the (possibly
        updated) spec.

        Returns (exit_code, updated_spec_content).
        """
        agent_config = get_agent(agent)
        spec_path = "/tmp/spec.md"

        # Write spec into sandbox (-i keeps stdin open for piping)
        write_proc = subprocess.run(
            ["sbx", "exec", "-i", sandbox_name,
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
                ["sbx", "exec", "-i", sandbox_name,
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
            cmd = ["sbx", "exec",
                   "-w", self._worktree_path]
            if env_vars:
                for k, v in env_vars.items():
                    cmd.extend(["-e", f"{k}={v}"])
            cmd.extend([
                sandbox_name, cli_command,
                "-p", self.iteration_prompt(spec_path),
                "--model", model,
            ] + cli_flags)
            rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, check=False).returncode
        else:
            env_var_name = agent_config["env_var_name"]
            inner_cmd = (
                f'export {env_var_name}="$(cat {secret_path})" && '
                f"rm {secret_path} && "
                f"exec {cli_command} -p "
                + shlex.quote(self.iteration_prompt(spec_path))
                + f" --model {shlex.quote(model)}"
            )
            for flag in cli_flags:
                inner_cmd += f" {shlex.quote(flag)}"
            cmd = ["sbx", "exec",
                   "-w", self._worktree_path,
                   sandbox_name, "sh", "-c", inner_cmd]
            rc = subprocess.run(cmd, stdin=subprocess.DEVNULL, check=False).returncode

        # Read back (possibly updated) spec
        read_proc = subprocess.run(
            ["sbx", "exec", sandbox_name, "cat", spec_path],
            stdout=subprocess.PIPE, text=True, check=False,
        )
        updated = read_proc.stdout if read_proc.returncode == 0 else spec_content

        return rc, updated

    # -- Selftest --------------------------------------------------------------

    def remove_sandbox(self, name):
        """Remove a sandbox by name (best-effort)."""
        subprocess.run(
            ["sbx", "rm", "--force", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        self._remove_sandbox_timestamp(name)

    # -- Pre-flight validation ------------------------------------------------

    def _preflight_backend_checks(self, sandbox_name):
        """Docker sandbox-specific pre-flight checks: sandbox responsiveness and network policy."""
        failures = []
        sandbox_ok = False
        result = subprocess.run(
            ["sbx", "exec", sandbox_name, "echo", "ok"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(
                f"sandbox {sandbox_name} is not responding"
                f" — try: sbx rm --force {sandbox_name}")
        else:
            sandbox_ok = True

        # Network policy applied (only if sandbox is responsive).
        # Use -sf so curl exits non-zero on HTTP 4xx (sbx gateway blocks via
        # MITM 403, not TCP-level refusal, so -f is required to detect blocking).
        if sandbox_ok:
            result = subprocess.run(
                ["sbx", "exec", sandbox_name,
                 "curl", "-sf", "--max-time", "3", "https://google.com"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            if result.returncode == 0:
                failures.append(
                    f"network policy not applied to sandbox {sandbox_name}"
                    " — outbound requests should be blocked")

        return failures
