"""Tart macOS VM sandbox backend for ralph agent-loop isolation."""

import atexit
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from ralph.sandbox import SandboxBackend


class TartSandbox(SandboxBackend):
    """Manages Tart macOS VM sandboxes for agent-loop isolation.

    Uses Tart (Virtualization.framework) to run macOS VMs on Apple Silicon.
    Content-addressed templates cache dependency installs; per-branch VMs
    are APFS clones of templates.
    """

    SHARED_DIR = "/Volumes/My Shared Files/workspace"
    SHARED_DIR_GITDIR = "/Volumes/My Shared Files/gitdir"
    _vm_procs = {}
    _vm_list_cache = (0, [])

    def __init__(self, dotfiles_dir, config=None):
        self.dotfiles_dir = dotfiles_dir
        config = config or {}
        self.base_image = config.get("base_image", "")
        self.dependencies_content = config.get("dependencies_content", "")

    def check_prerequisites(self):
        """Check that tart and docker (for proxy) are available.

        Returns list of error messages. Empty list means all checks passed.
        """
        errors = []
        if not shutil.which("tart"):
            errors.append(
                "tart is not installed"
                " (install: brew install cirruslabs/cli/tart)")
        if not shutil.which("docker"):
            errors.append(
                "docker is not installed"
                " (required for the credential proxy)")
        return errors

    def _template_name(self, agent):
        """Compute content-addressed template VM name.

        Hash is SHA256(base_image + newline + dependencies_content)[:12].
        """
        content = self.base_image + "\n" + self.dependencies_content
        h = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"agent-loop-template-{agent}-{h}"

    _VM_LIST_CACHE_TTL = 2  # seconds

    def _list_vms(self):
        """List Tart VMs via 'tart list --format json'. Returns parsed list.

        Caches the result for up to _VM_LIST_CACHE_TTL seconds to avoid
        redundant subprocess calls when multiple methods query VM state
        in quick succession.
        """
        now = time.monotonic()
        cached_time, cached_result = TartSandbox._vm_list_cache
        if now - cached_time < self._VM_LIST_CACHE_TTL:
            return cached_result

        result = subprocess.run(
            ["tart", "list", "--format", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            vms = []
        else:
            try:
                vms = json.loads(result.stdout)
            except (json.JSONDecodeError, ValueError):
                vms = []
        TartSandbox._vm_list_cache = (now, vms)
        return vms

    def _vm_exists(self, name):
        """Check if a VM with the given name exists."""
        for vm in self._list_vms():
            if vm.get("Name") == name:
                return True
        return False

    def _vm_state(self, name):
        """Return the state of a VM by name, or None if not found."""
        for vm in self._list_vms():
            if vm.get("Name") == name:
                return vm.get("State")
        return None

    def _running_vm_count(self):
        """Count running macOS VMs."""
        return sum(1 for vm in self._list_vms() if vm.get("State") == "Running")

    def _check_vm_limit(self):
        """Raise RuntimeError if at the Apple SLA VM limit (max 2)."""
        count = self._running_vm_count()
        if count >= 2:
            raise RuntimeError(
                f"ralph: cannot start VM — {count} macOS VMs already running "
                f"(Apple SLA permits max 2). "
                f"Stop an existing VM with: tart stop <name>")

    def _wait_for_guest_agent(self, vm_name, timeout=120):
        """Poll tart exec until guest agent responds or timeout.

        Tries 'tart exec <vm> echo ok' every 2 seconds.
        Raises RuntimeError on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = subprocess.run(
                ["tart", "exec", vm_name, "echo", "ok"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            if result.returncode == 0:
                return
            time.sleep(2)
        raise RuntimeError(
            f"ralph: guest agent not responding on VM {vm_name} "
            f"after {timeout}s timeout")

    def ensure_image(self, agent, force_rebuild=False):
        """Ensure template VM is built and cached. Returns template name.

        Clones from base image and optionally installs dependencies.
        """
        template = self._template_name(agent)

        # Check cache
        if not force_rebuild and self._vm_exists(template):
            print(f"ralph: using cached template {template}")
            return template

        # Force rebuild: delete old template first
        if force_rebuild and self._vm_exists(template):
            print(f"ralph: deleting old template {template}")
            subprocess.run(
                ["tart", "delete", template],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )

        # Clone base image
        print(f"ralph: cloning base image {self.base_image} → {template}")
        subprocess.run(
            ["tart", "clone", self.base_image, template], check=True)

        # Install dependencies if any
        if self.dependencies_content.strip():
            self._check_vm_limit()
            print(f"ralph: installing dependencies in template {template}")
            vm_proc = subprocess.Popen(
                ["tart", "run", template, "--no-graphics"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                self._wait_for_guest_agent(template)
                subprocess.run(
                    ["tart", "exec", "-i", template, "bash", "-e"],
                    input=self.dependencies_content, text=True, check=True,
                )
            finally:
                subprocess.run(
                    ["tart", "stop", template],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
                vm_proc.wait()

        return template

    # -- Sandbox lifecycle ----------------------------------------------------

    _atexit_registered = False

    @staticmethod
    def exec_output(sandbox_name, *cmd):
        """Run a command inside the VM and return its stdout (stripped)."""
        result = subprocess.run(
            ["tart", "exec", sandbox_name] + list(cmd),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _resolve_git_common_dir(worktree_path):
        """Resolve the shared .git directory for a worktree.

        A worktree's .git is a file containing 'gitdir: <path>' pointing
        into the main repo's .git/worktrees/<name>/ directory.  The common
        dir is two levels up from that.

        Returns the absolute path to the repo's common .git directory,
        or None if this is a regular repo (not a worktree).
        """
        git_path = os.path.join(worktree_path, ".git")
        if not os.path.isfile(git_path):
            return None
        with open(git_path) as f:
            line = f.readline().strip()
        if not line.startswith("gitdir: "):
            return None
        gitdir = line[len("gitdir: "):]
        if not os.path.isabs(gitdir):
            gitdir = os.path.join(worktree_path, gitdir)
        # gitdir is .git/worktrees/<name> — common dir is two levels up
        common = os.path.dirname(os.path.dirname(gitdir))
        return os.path.realpath(common)

    def _setup_git_common_dir_symlink(self, sandbox_name, git_common_dir):
        """Create a symlink inside the VM so the worktree .git pointer resolves.

        VirtioFS mounts the git common dir at /Volumes/My Shared Files/gitdir,
        but the worktree's .git file contains the host's absolute path. This
        creates the host path as a symlink to the VirtioFS mount so git can
        follow the pointer.
        """
        parent = os.path.dirname(git_common_dir)
        subprocess.run(
            ["tart", "exec", sandbox_name,
             "bash", "-c", f"mkdir -p {shlex.quote(parent)} && "
             f"ln -s {shlex.quote(self.SHARED_DIR_GITDIR)} "
             f"{shlex.quote(git_common_dir)}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    def ensure_sandbox(self, agent, branch, worktree_path, **kwargs):
        """Ensure a per-branch VM exists and is running. Returns VM name.

        Clones from the template. If the VM exists but is stopped, deletes
        and recreates. Reuses a running VM. Registers an atexit handler
        (once) to stop all tracked VMs on exit.

        When the workspace is a git worktree, the repo's shared .git
        directory is mounted as a second VirtioFS share so that the
        worktree's .git pointer resolves inside the VM.
        """
        name = self.sandbox_name(agent, branch)
        state = self._vm_state(name)

        if state == "Running":
            print(f"ralph: reusing running VM {name}")
            return name

        if state is not None:
            # Exists but stopped — delete and recreate
            print(f"ralph: deleting stopped VM {name}")
            subprocess.run(
                ["tart", "delete", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )

        self._check_vm_limit()

        template = self.ensure_image(agent,
                                     force_rebuild=kwargs.get("force_rebuild", False))
        print(f"ralph: cloning VM {name} from template {template}")
        subprocess.run(["tart", "clone", template, name], check=True)

        # Resolve git common dir for worktrees
        git_common_dir = self._resolve_git_common_dir(worktree_path)

        # Start headless with directory sharing
        print(f"ralph: starting VM {name}")
        run_cmd = ["tart", "run", name, "--no-graphics",
                   f"--dir=workspace:{worktree_path}"]
        if git_common_dir:
            run_cmd.append(f"--dir=gitdir:{git_common_dir}")
        vm_proc = subprocess.Popen(
            run_cmd,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._vm_procs[name] = vm_proc

        # Register atexit handler once to stop all tracked VMs
        if not TartSandbox._atexit_registered:
            atexit.register(TartSandbox._atexit_stop_all)
            TartSandbox._atexit_registered = True

        self._wait_for_guest_agent(name)

        # Create symlink inside VM so the worktree's .git file resolves
        if git_common_dir:
            self._setup_git_common_dir_symlink(name, git_common_dir)

        return name

    @staticmethod
    def _atexit_stop_all():
        """Stop all tracked VMs on interpreter exit (best-effort).

        Iterates the class-level _vm_procs dict, stops each VM, waits for
        each Popen process, then clears the dict. Errors are suppressed
        since this runs during interpreter shutdown.
        """
        for name, proc in list(TartSandbox._vm_procs.items()):
            try:
                subprocess.run(
                    ["tart", "stop", name],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
        TartSandbox._vm_procs.clear()

    def setup_git_config(self, sandbox_name, user, email):
        """Configure git user and safe directory settings inside the VM."""
        subprocess.run(
            ["tart", "exec", sandbox_name,
             "git", "config", "--global", "user.name", user],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["tart", "exec", sandbox_name,
             "git", "config", "--global", "user.email", email],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        subprocess.run(
            ["tart", "exec", sandbox_name,
             "git", "config", "--global", "--add", "safe.directory", "*"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None):
        """Run a single Claude Code iteration inside the VM.

        Writes spec to /tmp/spec.md via tart exec, runs claude with env vars,
        then reads back the (possibly updated) spec.

        Returns (exit_code, updated_spec_content).
        """
        spec_path = "/tmp/spec.md"

        # Write spec into VM
        write_proc = subprocess.run(
            ["tart", "exec", "-i", sandbox_name, "tee", spec_path],
            input=spec_content, text=True, check=False,
            stdout=subprocess.DEVNULL,
        )
        if write_proc.returncode != 0:
            return write_proc.returncode, spec_content

        # Build claude command with env vars, shell-escaped
        env_prefix = ""
        if env_vars:
            parts = [f"{k}={shlex.quote(v)}" for k, v in env_vars.items()]
            env_prefix = "env " + " ".join(parts) + " "
        claude_cmd = (
            f"cd '{self.SHARED_DIR}' && "
            f"{env_prefix}claude "
            f"-p {shlex.quote(self.ITERATION_PROMPT)} "
            f"--model {shlex.quote(model)} "
            f"--dangerously-skip-permissions "
            f"--effort high"
        )
        rc = subprocess.run(
            ["tart", "exec", sandbox_name, "bash", "-c", claude_cmd],
            check=False,
        ).returncode

        # Read back (possibly updated) spec
        read_proc = subprocess.run(
            ["tart", "exec", sandbox_name, "cat", spec_path],
            stdout=subprocess.PIPE, text=True, check=False,
        )
        updated = read_proc.stdout if read_proc.returncode == 0 else spec_content

        return rc, updated

    def proxy_host(self):
        """Return the host IP reachable from inside the Tart VM.

        Tries to discover the gateway from inside a running VM, falls back
        to ipconfig on the host, then to the well-known Tart NAT gateway.
        Caches the result after first discovery.
        """
        if hasattr(self, "_cached_proxy_host"):
            return self._cached_proxy_host

        # Try to get gateway from a running VM
        for name, proc in self._vm_procs.items():
            if proc.poll() is None:  # still running
                gw = self.exec_output(
                    name, "route", "-n", "get", "default")
                for line in gw.splitlines():
                    line = line.strip()
                    if line.startswith("gateway:"):
                        ip = line.split(":", 1)[1].strip()
                        if ip:
                            self._cached_proxy_host = ip
                            return ip
                break

        # Fallback: ipconfig on host
        result = subprocess.run(
            ["ipconfig", "getifaddr", "en0"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            self._cached_proxy_host = result.stdout.strip()
            return self._cached_proxy_host

        # Final fallback
        self._cached_proxy_host = "192.168.64.1"
        return self._cached_proxy_host

    def check_in_sync(self, sandbox_name, work_dir, git):
        """Check if VM content matches host worktree HEAD.

        Tart uses VirtioFS shared directories, so the VM and host share
        the same filesystem. Always returns True.
        """
        return True

    def reset_to_host(self, sandbox_name, work_dir, git):
        """Reset VM git state to match host worktree.

        Tart uses VirtioFS shared directories — no-op since files are shared.
        """
        return True

    def sync_to_host(self, sandbox_name, head_before, head_after, work_dir):
        """Sync commits from VM to host worktree.

        Tart uses VirtioFS shared directories, so commits made inside the VM
        are immediately visible on the host. Verify the commit exists.
        """
        result = subprocess.run(
            ["git", "rev-parse", "--verify", head_after],
            cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode == 0:
            print(f"ralph: commit {head_after[:8]} visible on host (shared filesystem)")
            return True
        print("ralph: error: commit not visible on host despite shared filesystem",
              file=sys.stderr)
        return False

    def cleanup_sandbox(self, agent, branch):
        """Stop and delete the VM for a given agent and branch."""
        name = self.sandbox_name(agent, branch)
        print(f"ralph: stopping VM {name}")
        subprocess.run(
            ["tart", "stop", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        # Wait for the Popen process if we're tracking it
        proc = self._vm_procs.pop(name, None)
        if proc is not None:
            proc.wait()
        print(f"ralph: deleting VM {name}")
        subprocess.run(
            ["tart", "delete", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    def remove_sandbox(self, name):
        """Remove a VM by name (best-effort)."""
        subprocess.run(
            ["tart", "stop", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        proc = self._vm_procs.pop(name, None)
        if proc is not None:
            proc.wait()
        subprocess.run(
            ["tart", "delete", name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    def prune_sandboxes(self, agent):
        """Remove orphaned VMs whose workspace worktree paths no longer exist.

        Looks for VMs matching the agent-loop-{agent}-* prefix (excluding
        templates). Returns list of pruned VM names.
        """
        prefix = f"agent-loop-{agent}-"
        template_prefix = f"agent-loop-template-{agent}-"
        pruned = []
        for vm in self._list_vms():
            name = vm.get("Name", "")
            if not name.startswith(prefix):
                continue
            if name.startswith(template_prefix):
                continue
            # VM is an agent-loop sandbox — check if we can determine its worktree
            # Since Tart VMs don't track workspace path in metadata, we consider
            # non-running VMs as orphans eligible for pruning
            state = vm.get("State", "")
            if state == "Running":
                continue
            print(f"ralph: pruning orphan VM {name}")
            subprocess.run(
                ["tart", "stop", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                ["tart", "delete", name],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                check=False,
            )
            pruned.append(name)
        return pruned

    def _preflight_backend_checks(self, sandbox_name):
        """Tart-specific pre-flight checks: VM responsiveness."""
        failures = []
        result = subprocess.run(
            ["tart", "exec", sandbox_name, "echo", "ok"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        if result.returncode != 0:
            failures.append(
                f"VM {sandbox_name} is not responding"
                f" — try: tart stop {sandbox_name} && tart delete {sandbox_name}")
        else:
            # Log note about network isolation
            print("ralph: note: Tart VMs do not have network isolation enabled")

        return failures
