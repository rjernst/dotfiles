"""nono runtime backend for ralph agent-loop isolation.

Runs the agent directly on the host under ``nono run``, which applies
kernel sandboxing (Seatbelt on macOS, Landlock+seccomp on Linux) to the
child process.  There is no image to build: the host toolchain is used as
is.  Credentials are injected by nono's own per-session credential proxy,
so ralph's TCP credential proxy is never started for this backend.

Per-sandbox state lives inside the project's shared git directory, so it
dies with the repository it belongs to and never appears in ``git
status``.  The sandbox is granted neither: the git common dir is read-only
to it (see ``nono_profile.git_grants``).

    <git-common-dir>/ralph/nono/<sandbox>/profile.json  generated nono profile
    <git-common-dir>/ralph/nono/<sandbox>/project.json  project profile, if any
    <git-common-dir>/ralph/nono/<sandbox>/gitconfig     GIT_CONFIG_GLOBAL
    <git-common-dir>/ralph/nono/<sandbox>/worktree      worktree path, for pruning
    <git-common-dir>/ralph/nono/<sandbox>/spec-XXXX/    per-iteration spec dir

Two paths stay under ``~/.ralph`` because they are shared by every
project:

    ~/.ralph/claude-config                      CLAUDE_CONFIG_DIR for the agent
    ~/.ralph/docker-proxy.sock                  filtered Docker API socket

The user's real ``~/.claude`` and ``~/.gitconfig`` are never granted to
the sandbox and never written.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

from ralph.agents import get_agent
from ralph.docker_proxy import ensure_docker_proxy_socket
from ralph.runtime import DEFAULT_NETWORK_MODE, NETWORK_MODES, Runtime
from ralph.runtime.nono_profile import (
    build_profile,
    ephemeral_port_range,
    project_profile_source,
    write_project_profile,
)
from ralph.token import keychain_service_name, keystore_read_command

# Oldest nono that supports everything the generated profile uses
# (``cmd://`` credential capture, ``--allow-unix-socket``).
MIN_NONO_VERSION = (0, 77, 0)

# Project files the Docker backends build an image from.  The nono runtime
# uses the host toolchain, so they mean nothing here and are called out
# rather than silently ignored.
IGNORED_PROJECT_FILES = ("Dockerfile.sandbox", "dependencies")

# Environment variables dropped from the loop's ``build_proxy_env`` result
# before running the agent: nono's own proxy sets the base URL, and the
# phantom token would only shadow the credential nono injects.
DROPPED_ENV_VARS = frozenset({
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
})

# Name of the project profile copy a generated profile extends.  nono
# resolves ``extends`` against the generated profile's own directory
# first, so the copy sits beside it and needs no global installation.
PROJECT_PROFILE_NAME = "project"


class NonoRuntime(Runtime):
    """Manages host-side nono sandboxes for agent-loop isolation."""

    # nono injects the Anthropic credential itself, via a per-session
    # proxy fed by a ``cmd://`` capture that runs ``ralph get-token``.
    uses_credential_proxy = False

    # The agent runs on the host, so ralph's remaining host-side proxies
    # are reachable over loopback and need not be exposed further.
    PROXY_LISTEN_ADDR = "127.0.0.1"

    def __init__(self, dotfiles_dir, allowed_hosts=None,
                 network=DEFAULT_NETWORK_MODE, project_dir=None,
                 auth_mode=None, token_data=None):
        if network not in NETWORK_MODES:
            raise ValueError(
                f"ralph: unknown network mode {network!r} "
                f"(expected 'filtered' or 'unrestricted')")
        self.dotfiles_dir = dotfiles_dir
        self.allowed_hosts = tuple(allowed_hosts) if allowed_hosts else ()
        self.network = network
        self.project_dir = project_dir
        self.auth_mode = auth_mode
        self.token_data = token_data
        # Resolved once so a caller with an overridden HOME gets a
        # consistent set of paths for the lifetime of the runtime.
        self.home = os.path.expanduser("~")
        self._worktree_path = None
        self._git_common_dir = None
        self._project_profile_name = None
        self._warned_files = set()

    def proxy_host(self):
        """Return the host the sandboxed agent reaches proxies on.

        nono runs the agent on the host, so anything ralph exposes is on
        loopback.  The Anthropic base URL built from this is discarded by
        ``run_iteration`` — nono sets its own.
        """
        return "127.0.0.1"

    # -- Paths ----------------------------------------------------------------

    def git_common_dir(self):
        """Shared git directory of the project this runtime serves.

        Resolved once, from the project directory when the caller gave one
        and otherwise from the current directory, so ``prune-sandboxes``
        works from anywhere inside the repo.  Raises RuntimeError when
        there is no repository to anchor state to.
        """
        if self._git_common_dir:
            return self._git_common_dir
        base = self.project_dir or self._worktree_path or os.getcwd()
        common_dir = self._resolve_git_common_dir(base)
        if not common_dir:
            raise RuntimeError(f"ralph: {base} is not a git repository")
        self._git_common_dir = common_dir
        return common_dir

    def state_root(self):
        """Directory holding one state directory per sandbox."""
        return os.path.join(self.git_common_dir(), "ralph", "nono")

    def state_dir(self, name):
        """State directory for a single sandbox."""
        return os.path.join(self.state_root(), name)

    def profile_path(self, name):
        """Generated nono profile for a single sandbox."""
        return os.path.join(self.state_dir(name), "profile.json")

    def project_profile_path(self, name):
        """Copy of the project's own profile, extended by the generated one."""
        return os.path.join(self.state_dir(name),
                            f"{PROJECT_PROFILE_NAME}.json")

    def gitconfig_path(self, name):
        """GIT_CONFIG_GLOBAL the sandboxed agent reads."""
        return os.path.join(self.state_dir(name), "gitconfig")

    def worktree_file(self, name):
        """File recording which worktree a sandbox belongs to."""
        return os.path.join(self.state_dir(name), "worktree")

    def claude_config_dir(self):
        """CLAUDE_CONFIG_DIR for the sandboxed agent.

        Deliberately not the user's ``~/.claude``, which the sandbox can
        neither read nor write.
        """
        return os.path.join(self.home, ".ralph", "claude-config")

    def docker_socket_path(self):
        """Unix socket ralph's filtered Docker API proxy listens on."""
        return os.path.join(self.home, ".ralph", "docker-proxy.sock")

    # -- Prerequisites --------------------------------------------------------

    @staticmethod
    def _parse_version(output):
        """Extract the first X.Y.Z from ``nono --version`` output."""
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", output or "")
        if not m:
            return None
        return tuple(int(part) for part in m.groups())

    def _nono_version(self):
        """Return the installed nono version as a tuple, or None."""
        try:
            result = subprocess.run(
                ["nono", "--version"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
        except OSError:
            return None
        if result.returncode != 0:
            return None
        return self._parse_version(result.stdout or result.stderr)

    def check_prerequisites(self):
        """Check that nono, claude, and docker are available.

        Returns a list of error messages. Empty means all checks passed.
        """
        errors = []
        if not shutil.which("nono"):
            errors.append(
                "nono is not installed (install: see https://nono.sh/docs)")
        else:
            version = self._nono_version()
            required = ".".join(str(p) for p in MIN_NONO_VERSION)
            if version is None:
                errors.append(
                    "could not determine nono version"
                    f" — {required} or newer is required")
            elif version < MIN_NONO_VERSION:
                found = ".".join(str(p) for p in version)
                errors.append(
                    f"nono {found} is too old — {required} or newer is required")
        if not shutil.which("claude"):
            errors.append("claude is not installed")
        if not shutil.which("docker"):
            errors.append(
                "docker is not installed (required for the docker socket proxy)")
        return errors

    # -- Images (not used by this backend) ------------------------------------

    def _warn_ignored_project_files(self, project_dir):
        """Warn once per file about project config this backend ignores."""
        if not project_dir:
            return
        for filename in IGNORED_PROJECT_FILES:
            path = os.path.join(project_dir, ".agent-loop", filename)
            if path in self._warned_files or not os.path.isfile(path):
                continue
            self._warned_files.add(path)
            print(f"ralph: warning: {path} is ignored by the nono runtime",
                  file=sys.stderr)

    def ensure_image(self, agent, force_rebuild=False):
        """No-op: the sandbox runs the host toolchain. Returns "host"."""
        self._warn_ignored_project_files(self.project_dir)
        return "host"

    def ensure_project_image(self, agent, base_tag, project_dir,
                             force_rebuild=False):
        """No-op: there is no project image layer. Returns "host"."""
        self._warn_ignored_project_files(project_dir)
        return "host"

    # -- Sandbox lifecycle ----------------------------------------------------

    @staticmethod
    def _rev_parse(path, flag):
        """Return an absolute git directory for ``path``, or None."""
        result = subprocess.run(
            ["git", "rev-parse", flag],
            cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if not raw:
            return None
        return os.path.realpath(os.path.join(path, raw))

    @classmethod
    def _resolve_git_common_dir(cls, worktree_path):
        """Resolve the shared .git directory for a worktree (or regular repo).

        Returns the absolute path to the repo's .git directory, or None if
        git rev-parse fails (e.g. not a git repo).
        """
        return cls._rev_parse(worktree_path, "--git-common-dir")

    @classmethod
    def _resolve_git_dir(cls, worktree_path):
        """Resolve a worktree's own git directory.

        For a linked worktree this is ``<common>/worktrees/<name>``, which
        holds its HEAD, index and reflog; for a plain checkout it is the
        common dir itself.
        """
        return cls._rev_parse(worktree_path, "--git-dir")

    def _read_worktree(self, name):
        """Read the worktree path recorded for a sandbox, or None."""
        try:
            with open(self.worktree_file(name)) as f:
                return f.read().strip() or None
        except OSError:
            return None

    def _worktree_for(self, name):
        """Worktree of a sandbox, from this session or its state dir."""
        return self._worktree_path or self._read_worktree(name)

    @staticmethod
    def _make_private_dir(path):
        """Create a directory (if needed) and make it owner-only."""
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o700)

    def ensure_sandbox(self, agent, branch, worktree_path, project_dir=None,
                       force_rebuild=False):
        """Prepare host-side state for a sandbox. Returns the sandbox name.

        There is nothing to create or boot: the "sandbox" is the profile
        and the state directory that ``nono run`` is handed per iteration.
        """
        name = self.sandbox_name(agent, branch)
        self._worktree_path = worktree_path

        project_dir = project_dir or self.project_dir
        self._warn_ignored_project_files(project_dir)

        state_dir = self.state_dir(name)
        self._make_private_dir(state_dir)
        # git creates the reflog dir lazily, and the profile grants it by
        # path: without it the agent's first commit would be denied.
        os.makedirs(os.path.join(self.git_common_dir(), "logs"), exist_ok=True)
        with open(self.worktree_file(name), "w") as f:
            f.write(os.path.abspath(worktree_path) + "\n")

        if project_dir:
            self._project_profile_name = write_project_profile(
                project_dir, self.project_profile_path(name))
            if self._project_profile_name:
                print(f"ralph: extending project nono profile "
                      f"{project_profile_source(project_dir)}")

        self._make_private_dir(self.claude_config_dir())
        ensure_docker_proxy_socket(self.dotfiles_dir,
                                   socket_path=self.docker_socket_path())

        self._touch_sandbox_timestamp(name)
        print(f"ralph: using nono sandbox {name}")
        return name

    def cleanup_sandbox(self, agent, branch):
        """Remove the state directory for a given agent and branch."""
        name = self.sandbox_name(agent, branch)
        print(f"ralph: removing nono sandbox {name}")
        self.remove_sandbox(name)

    def remove_sandbox(self, name):
        """Remove a sandbox's state directory (best-effort)."""
        shutil.rmtree(self.state_dir(name), ignore_errors=True)
        self._remove_sandbox_timestamp(name)

    def prune_sandboxes(self, agent, max_age_days=None):
        """Remove orphaned or stale sandbox state directories.

        A sandbox is pruned if its recorded worktree no longer exists OR
        if it has not been used within max_age_days.  Sandboxes with no
        recorded timestamp are treated as stale.  Each sandbox's profiles
        live in its own state directory and go with it.

        Returns the list of pruned sandbox names.
        """
        if max_age_days is None:
            max_age_days = self.PRUNE_MAX_AGE_DAYS
        prefix = f"agent-loop-{agent}-"
        cutoff = time.time() - max_age_days * 86400
        pruned = []
        try:
            entries = sorted(os.listdir(self.state_root()))
        except OSError:
            entries = []
        for name in entries:
            if not name.startswith(prefix):
                continue
            if not os.path.isdir(self.state_dir(name)):
                continue
            worktree = self._read_worktree(name)
            if not worktree or not os.path.exists(worktree):
                print(f"ralph: pruning orphan sandbox {name}")
            else:
                last_used = self._sandbox_last_used(name)
                if last_used is not None and last_used >= cutoff:
                    continue
                print(f"ralph: pruning stale sandbox {name}")
            self.remove_sandbox(name)
            pruned.append(name)
        return pruned

    # -- Iteration ------------------------------------------------------------

    def setup_git_config(self, sandbox_name, user, email):
        """Write the gitconfig the sandboxed agent reads.

        The agent runs on the host, so ``git config --global`` would edit
        the user's real ``~/.gitconfig``.  Instead the config is written
        into the sandbox state dir and handed over as GIT_CONFIG_GLOBAL.
        """
        self._make_private_dir(self.state_dir(sandbox_name))
        content = (
            "[user]\n"
            f"\tname = {user}\n"
            f"\temail = {email}\n"
            # The sandbox has no access to any signing key, so asking for a
            # signature would only fail the commit.
            "[commit]\n"
            "\tgpgsign = false\n"
            "[tag]\n"
            "\tgpgsign = false\n"
            "[safe]\n"
            "\tdirectory = *\n"
            # Repacking rewrites the git common dir root, which the sandbox
            # only has read access to — keep git from trying.
            "[gc]\n"
            "\tauto = 0\n"
            "[maintenance]\n"
            "\tauto = false\n"
        )
        with open(self.gitconfig_path(sandbox_name), "w") as f:
            f.write(content)

    def _claude_bin(self):
        """Absolute path to the host's claude executable."""
        claude_bin = shutil.which("claude")
        if not claude_bin:
            raise RuntimeError(
                "ralph: claude is not installed — the nono runtime runs it "
                "from the host PATH")
        return claude_bin

    def _build_profile(self, sandbox_name, agent="claude", spec_dir=None):
        """Build the generated profile and write it to the state dir.

        Returns the path the profile was written to.
        """
        worktree = self._worktree_for(sandbox_name)
        if not worktree:
            raise RuntimeError(
                f"ralph: no worktree recorded for nono sandbox {sandbox_name}")
        git_common_dir = self._resolve_git_common_dir(worktree)
        git_dir = self._resolve_git_dir(worktree)
        if not git_common_dir or not git_dir:
            raise RuntimeError(
                f"ralph: {worktree} is not a git repository")
        port_range = (ephemeral_port_range()
                      if self.network == "filtered" else None)
        profile = build_profile(
            sandbox_name=sandbox_name,
            worktree=os.path.abspath(worktree),
            git_common_dir=git_common_dir,
            git_dir=git_dir,
            state_dir=self.state_dir(sandbox_name),
            home=self.home,
            path_env=os.environ.get("PATH", ""),
            claude_bin=self._claude_bin(),
            allowed_hosts=(list(get_agent(agent)["allowed_hosts"])
                           + list(self.allowed_hosts)),
            network=self.network,
            auth_mode=self.auth_mode,
            token_data=self.token_data,
            dotfiles_dir=self.dotfiles_dir,
            spec_dir=spec_dir,
            port_range=port_range,
            project_profile_name=self._project_profile_name,
        )
        path = self.profile_path(sandbox_name)
        self._make_private_dir(self.state_dir(sandbox_name))
        with open(path, "w") as f:
            json.dump(profile, f, indent=2)
            f.write("\n")
        return path

    def _nono_run_command(self, profile_path, worktree, command):
        """Build the ``nono run`` argv wrapping a host command."""
        cmd = [
            "nono", "run",
            "-p", profile_path,
            "--workdir", worktree,
            "--no-rollback",
            "--suppress-save-prompt",
            "--allow-unix-socket", self.docker_socket_path(),
        ]
        if self.network == "unrestricted":
            cmd.append("--allow-net")
        return cmd + ["--"] + list(command)

    def _iteration_env(self, sandbox_name, env_vars=None):
        """Environment for the ``nono run`` process.

        The loop's proxy env vars are carried through for their model
        settings, minus the phantom token and base URL — nono's own proxy
        owns both.  Those names are also stripped from the inherited host
        environment: a token or a base URL pointing at some other proxy
        must never reach the agent.
        """
        env = dict(os.environ)
        env.update(env_vars or {})
        for key in DROPPED_ENV_VARS:
            env.pop(key, None)
        env.update({
            "CLAUDE_CONFIG_DIR": self.claude_config_dir(),
            "GIT_CONFIG_GLOBAL": self.gitconfig_path(sandbox_name),
            "DOCKER_HOST": f"unix://{self.docker_socket_path()}",
            "NONO_NO_UPDATE_CHECK": "1",
            "NONO_NO_PACK_UPDATE_HINTS": "1",
            "NONO_NO_MIGRATE": "1",
        })
        return env

    def run_iteration(self, sandbox_name, spec_content, model, env_vars=None,
                      agent="claude", api_key=None):
        """Run a single agent iteration under ``nono run``.

        The spec is written into a per-iteration directory inside the
        state dir.  The *directory* is what the profile grants, not the
        file: a single-file grant does not survive the write-temp-then-
        rename that editors (Claude Code included) use, which would leave
        the agent unable to tick off its own tasks.  The directory is
        removed afterwards.

        Returns (exit_code, updated_spec_content).
        """
        if agent != "claude":
            raise ValueError("ralph: runtime nono only supports agent claude")
        agent_config = get_agent(agent)

        state_dir = self.state_dir(sandbox_name)
        self._make_private_dir(state_dir)
        spec_dir = tempfile.mkdtemp(prefix="spec-", dir=state_dir)
        spec_path = os.path.join(spec_dir, "spec.md")
        try:
            with open(spec_path, "w") as f:
                f.write(spec_content)

            profile_path = self._build_profile(sandbox_name, agent=agent,
                                               spec_dir=spec_dir)
            worktree = self._worktree_for(sandbox_name)
            cmd = self._nono_run_command(
                profile_path, worktree,
                [agent_config["cli_command"],
                 "-p", self.iteration_prompt(spec_path),
                 "--model", model] + agent_config["cli_flags"](model),
            )
            rc = subprocess.run(
                cmd, cwd=worktree, env=self._iteration_env(sandbox_name, env_vars),
                check=False,
            ).returncode

            try:
                with open(spec_path) as f:
                    updated = f.read()
            except OSError:
                updated = spec_content
            return rc, updated
        finally:
            shutil.rmtree(spec_dir, ignore_errors=True)

    # -- Host/sandbox sync ----------------------------------------------------

    def check_in_sync(self, sandbox_name, work_dir, git):
        """Check that the worktree's git state is usable.

        The agent works directly in the host worktree, so there are never
        two copies to reconcile — only a broken worktree to report.
        """
        return bool(git.output("rev-parse", "HEAD", cwd=work_dir))

    def reset_to_host(self, sandbox_name, work_dir, git):
        """Discard uncommitted changes left in the worktree."""
        head = git.output("rev-parse", "HEAD", cwd=work_dir)
        if not head:
            return False
        result = git.run("reset", "--hard", head, cwd=work_dir, check=False)
        if result.returncode != 0:
            return False
        git.run("clean", "-fd", cwd=work_dir, check=False)
        return True

    def sync_to_host(self, sandbox_name, head_before, head_after, work_dir):
        """Verify the agent's commits are visible on the host.

        The agent commits straight into the host worktree, so this only
        confirms the new HEAD resolves.
        """
        result = subprocess.run(
            ["git", "rev-parse", "--verify", head_after],
            cwd=work_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )
        if result.returncode != 0:
            print(f"ralph: error: host cannot see commit {head_after}",
                  file=sys.stderr)
            return False
        print(f"ralph: synced commits to {work_dir}")
        return True

    # -- Token store ----------------------------------------------------------

    def _token_store_read_command(self, agent="claude"):
        """Command that reads this backend's token store on this platform.

        Run *inside* the sandbox by the selftest, to prove the agent cannot
        reach the stored token.
        """
        return keystore_read_command(keychain_service_name(agent,
                                                           self.auth_mode))
