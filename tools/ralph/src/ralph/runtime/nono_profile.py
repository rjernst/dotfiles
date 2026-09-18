"""Builders for the nono sandbox profile used by the nono runtime.

``nono run -p <path>`` takes a JSON profile describing what the sandboxed
process may touch: filesystem grants, environment variables it inherits,
the network it may reach, and the credentials nono's per-session proxy
injects on its behalf.  This module builds that document.

Everything here is a plain value transformation apart from four
deliberate touches of the host: the ephemeral port range is read from
sysctl or procfs, PATH entries are filtered by existence, the ``claude``
binary is resolved through symlinks, and ``write_project_profile``
copies a project's profile beside the generated one.  No nono
process is ever started from here, and the real token never enters the
profile — it names a capture command that ralph runs host-side instead.
"""

import json
import os
import subprocess
import sys

from ralph.agents import get_agent, get_auth_mode
from ralph.runtime import NETWORK_MODES

# Schema version written into the generated profile's meta block.
PROFILE_VERSION = "1.0.0"

# Name of the nono credential capture that runs `ralph get-token`, and the
# credential key that refers to it from the custom credential route.
CAPTURE_NAME = "ralph_token"

# Upstream used when the stored token carries no baseUrl of its own
# (oauth, and api_key against the public API).
DEFAULT_UPSTREAM = "https://api.anthropic.com"

# Subdirectories of the git common dir a commit has to write.  Everything
# else there — config, hooks/, packed-refs — stays read-only.
GIT_WRITABLE_SUBDIRS = ("objects", "refs", "logs")

# nono's Seatbelt backend on macOS refuses port ranges wider than this.
MACOS_MAX_PORT_RANGE = 16384

# Where each platform publishes its ephemeral port range.
MACOS_PORTRANGE_FIRST = "net.inet.ip.portrange.first"
MACOS_PORTRANGE_LAST = "net.inet.ip.portrange.last"
LINUX_PORTRANGE_PATH = "/proc/sys/net/ipv4/ip_local_port_range"

# Environment variables the sandboxed agent inherits from ralph.  nono
# drops everything else, so anything the agent needs must be listed here.
# Entries ending in '*' are prefix wildcards.
ALLOW_VARS = [
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TERM",
    "LANG",
    "LC_*",
    "TMPDIR",
    "JAVA_HOME",
    "CLAUDE_CONFIG_DIR",
    "GIT_CONFIG_GLOBAL",
    "DOCKER_HOST",
    "ANTHROPIC_CUSTOM_MODEL_OPTION",
    "ANTHROPIC_DEFAULT_*_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
]

# How the injected credential is presented to the upstream API, per auth
# mode.  ``credential_format`` is a nono template: '{}' is replaced with
# the captured token by nono, not by Python.
CREDENTIAL_ROUTES = {
    "oauth": {
        "env_var": "CLAUDE_CODE_OAUTH_TOKEN",
        "inject_header": "Authorization",
        "credential_format": "Bearer {}",
    },
    "api_key": {
        "env_var": "ANTHROPIC_API_KEY",
        "inject_header": "x-api-key",
        "credential_format": "{}",
    },
    "gateway": {
        "env_var": "ANTHROPIC_AUTH_TOKEN",
        "inject_header": "Authorization",
        "credential_format": "Bearer {}",
    },
}


# ---------------------------------------------------------------------------
# Ephemeral port range
# ---------------------------------------------------------------------------

def _read_port_source(source):
    """Read the raw text a platform publishes its port range in.

    An absolute path is read as a file (Linux procfs); anything else is a
    sysctl name (macOS).  Both failure modes are reported as ValueError so
    callers only have to handle the one exception type.
    """
    if source.startswith("/"):
        try:
            with open(source) as f:
                return f.read()
        except OSError as exc:
            raise ValueError(f"ralph: could not read {source}: {exc}") from exc
    try:
        result = subprocess.run(
            ["sysctl", "-n", source],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            f"ralph: could not read sysctl {source}: {exc}") from exc
    return result.stdout


def ephemeral_port_range(platform=None, reader=_read_port_source):
    """Return the host's ephemeral port range as an inclusive (first, last).

    The sandbox needs these ports opened so tests and build daemons
    (Gradle, JVM forks) can talk to each other over loopback.

    ``platform`` defaults to ``sys.platform``, read at call time so tests
    that monkeypatch it are honoured.  ``reader`` maps a source — a sysctl
    name or a procfs path — to its raw text, and exists so tests can
    supply the values without touching the host.

    Raises ValueError if the range cannot be read or is malformed, or on
    macOS if it is wider than nono allows.
    """
    if platform is None:
        platform = sys.platform
    if platform == "darwin":
        first = _parse_port(reader(MACOS_PORTRANGE_FIRST), MACOS_PORTRANGE_FIRST)
        last = _parse_port(reader(MACOS_PORTRANGE_LAST), MACOS_PORTRANGE_LAST)
        if last - first + 1 > MACOS_MAX_PORT_RANGE:
            raise ValueError(
                "ralph: ephemeral port range exceeds nono macOS limit of "
                f"{MACOS_MAX_PORT_RANGE}")
    else:
        parts = reader(LINUX_PORTRANGE_PATH).split()
        if len(parts) != 2:
            raise ValueError(
                f"ralph: malformed ephemeral port range in "
                f"{LINUX_PORTRANGE_PATH}: {' '.join(parts)!r}")
        first = _parse_port(parts[0], LINUX_PORTRANGE_PATH)
        last = _parse_port(parts[1], LINUX_PORTRANGE_PATH)
    if last < first:
        raise ValueError(
            f"ralph: ephemeral port range is inverted: {first}-{last}")
    return (first, last)


def _parse_port(raw, source):
    """Parse a port number, reporting the source it came from on failure."""
    try:
        return int(raw.strip())
    except (AttributeError, ValueError):
        raise ValueError(
            f"ralph: malformed port number from {source}: {raw!r}") from None


# ---------------------------------------------------------------------------
# Credential routing
# ---------------------------------------------------------------------------

def normalize_auth_mode(auth_mode):
    """Return the internal auth mode name for a CLI-style mode.

    ``None`` resolves to the claude agent's default mode and "api-key" to
    "api_key", matching ralph.token's handling.  Validation is delegated to
    ``ralph.agents`` so an unknown mode reads the same here as everywhere
    else; ValueError is raised for anything the nono credential route
    cannot express.
    """
    if auth_mode is None:
        mode = get_agent("claude")["default_auth_mode"]
    else:
        mode = auth_mode.replace("-", "_")
    get_auth_mode("claude", mode)
    if mode not in CREDENTIAL_ROUTES:
        raise ValueError(
            f"ralph: auth mode {mode!r} has no nono credential route")
    return mode


def credential_route(auth_mode, token_data=None):
    """Build the nono custom credential route for the Anthropic API.

    nono's per-session proxy strips the phantom credential the agent sends
    and injects the real one, captured host-side by running
    ``ralph get-token``.  The agent never sees the token.
    """
    route = CREDENTIAL_ROUTES[normalize_auth_mode(auth_mode)]
    return {
        "upstream": (token_data or {}).get("baseUrl") or DEFAULT_UPSTREAM,
        "credential_key": f"cmd://{CAPTURE_NAME}",
        "env_var": route["env_var"],
        "inject_header": route["inject_header"],
        "credential_format": route["credential_format"],
    }


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

def _dedupe(paths):
    """Drop repeated paths, preserving first-seen order."""
    seen = set()
    result = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _require_abs(name, value):
    """Return an absolute path argument, or raise ValueError.

    Every path in the profile is a grant nono resolves on its own, so a
    missing or relative one would silently sandbox the wrong directory.
    Failing here beats an opaque permission error mid-iteration.
    """
    if not value:
        raise ValueError(f"ralph: nono profile requires {name}")
    if not os.path.isabs(value):
        raise ValueError(
            f"ralph: nono profile {name} must be an absolute path: {value}")
    return value


def _home_path_dirs(path_env, home):
    """Return the PATH entries that live under HOME and exist on disk.

    Paths outside HOME stay readable inside the sandbox, but HOME does
    not, so tool directories there (fnm, jenv, ~/bin, ...) need explicit
    read grants.  Entries that do not exist are dropped rather than handed
    to nono as grants for nothing.

    Note that a grant covers the directory, not the targets of symlinks
    inside it: a ``~/bin`` full of links into a dotfiles checkout needs
    that checkout granted as well, which is what
    ``.agent-loop/nono-profile.json`` is for.
    """
    prefix = home.rstrip("/") + "/"
    dirs = []
    for entry in (path_env or "").split(os.pathsep):
        if not entry:
            continue
        entry = os.path.normpath(entry)
        if entry.startswith(prefix) and os.path.isdir(entry):
            dirs.append(entry)
    return dirs


def git_grants(git_common_dir, git_dir):
    """Return the (read-write, read-only) git grants for a checkout.

    A commit writes objects, refs, reflogs and the per-worktree git dir —
    but not the common dir root, which also holds ``config`` and
    ``hooks/``.  Those stay read-only: git runs hooks and honours config
    such as ``core.fsmonitor`` on the *host*, outside the sandbox, so an
    agent able to write them could execute code the sandbox never sees.

    A plain (non-worktree) checkout keeps HEAD and the index in the common
    dir root, so it has to be granted read-write and this protection does
    not apply.  ralph itself always runs the agent in a linked worktree.
    """
    writable = [os.path.join(git_common_dir, name)
                for name in GIT_WRITABLE_SUBDIRS]
    if os.path.realpath(git_dir) == os.path.realpath(git_common_dir):
        writable.append(git_common_dir)
    else:
        writable.append(git_dir)
    return writable, [git_common_dir]


def build_profile(sandbox_name, worktree, git_common_dir, git_dir, state_dir,
                  home, path_env, claude_bin, allowed_hosts, network,
                  auth_mode, token_data, dotfiles_dir, spec_dir=None,
                  port_range=None, project_profile_name=None):
    """Build the generated nono profile for one sandbox.

    Args:
        sandbox_name: sandbox name from ``Runtime.sandbox_name``.
        worktree: absolute path to the git worktree (read-write).
        git_common_dir: absolute path to the shared git dir; granted
            read-only, with only the subdirs a commit writes opened up.
        git_dir: absolute path to this worktree's own git dir (read-write).
        state_dir: the sandbox state dir, ``<git-common-dir>/ralph/nono/<sandbox>``.
        home: the user's home directory.
        path_env: the PATH the agent will run with.
        claude_bin: path to the claude executable (symlinks resolved here).
        allowed_hosts: agent plus project hosts; deduped and sorted here.
        network: "filtered" or "unrestricted".
        auth_mode: CLI auth mode, or None for the agent default.
        token_data: stored token dict, read only for its ``baseUrl``.
        dotfiles_dir: dotfiles checkout, source of the ``ralph`` script.
        spec_dir: per-iteration spec directory to grant read-write, if
            any.  A directory, not the file, so an atomic
            write-temp-then-rename save still works.
        port_range: inclusive (first, last) ephemeral range; required in
            filtered mode.
        project_profile_name: name of the project profile copy sitting
            beside the generated one, or None.

    Raises ValueError for a missing or relative path argument, an unknown
    network mode or auth mode, or a filtered profile with no port range.
    """
    if network not in NETWORK_MODES:
        raise ValueError(
            f"ralph: unknown network mode {network!r} "
            f"(expected {' or '.join(repr(m) for m in NETWORK_MODES)})")

    worktree = _require_abs("worktree", worktree)
    git_common_dir = _require_abs("git_common_dir", git_common_dir)
    git_dir = _require_abs("git_dir", git_dir)
    state_dir = _require_abs("state_dir", state_dir)
    home = _require_abs("home", home)
    claude_bin = _require_abs("claude_bin", claude_bin)
    if spec_dir is not None:
        spec_dir = _require_abs("spec_dir", spec_dir)

    claude_config = os.path.join(home, ".ralph", "claude-config")

    profile = {"meta": {"name": f"ralph-{sandbox_name}",
                        "version": PROFILE_VERSION}}
    if project_profile_name:
        profile["extends"] = project_profile_name

    git_writable, git_readable = git_grants(git_common_dir, git_dir)
    profile["filesystem"] = {
        "allow": _dedupe([worktree] + git_writable
                         + ([spec_dir] if spec_dir else [])
                         + [claude_config, claude_config + ".lock"]),
        "read": _dedupe(
            git_readable
            + _home_path_dirs(path_env, home)
            + [os.path.dirname(os.path.realpath(claude_bin))]),
        "read_file": [os.path.join(state_dir, "gitconfig")],
    }

    profile["environment"] = {"allow_vars": list(ALLOW_VARS)}

    net = {}
    if network == "filtered":
        if not port_range:
            raise ValueError(
                "ralph: filtered network requires an ephemeral port range")
        net["allow_domain"] = sorted({
            host.strip().lower() for host in (allowed_hosts or [])
            if host and host.strip()
        })
        net["open_port_range"] = [[port_range[0], port_range[1]]]
    net["credentials"] = ["anthropic"]
    net["custom_credentials"] = {
        "anthropic": credential_route(auth_mode, token_data),
    }
    profile["network"] = net

    # nono sanitizes PATH for captures, so the command must be absolute.
    ralph_bin = os.path.abspath(os.path.join(dotfiles_dir, "scripts", "ralph"))
    cli_mode = normalize_auth_mode(auth_mode).replace("_", "-")
    profile["credential_capture"] = {
        CAPTURE_NAME: {
            "command": [ralph_bin, "get-token", "--agent", "claude",
                        "--auth", cli_mode],
            "timeout_secs": 10,
            "cache_ttl_secs": 300,
        },
    }
    return profile


# ---------------------------------------------------------------------------
# Project profile
# ---------------------------------------------------------------------------

def project_profile_source(project_dir):
    """Path a project's optional raw nono profile is read from."""
    return os.path.join(project_dir, ".agent-loop", "nono-profile.json")


def write_project_profile(project_dir, dest_path):
    """Copy ``.agent-loop/nono-profile.json`` beside the generated profile.

    nono resolves ``extends`` by name, looking first in the directory the
    profile it was handed lives in.  Writing the project's profile there,
    as ``project.json`` with ``meta.name`` rewritten to match, means
    nothing is installed into nono's global profile directory and the copy
    is removed along with the rest of the sandbox's state.

    The profile is otherwise copied verbatim, by design: it is the
    project's escape hatch for grants ralph cannot guess (``~/.gradle``,
    ``~/.jenv``, ``JAVA_HOME``).  Because nono merges inherited lists
    additively, a project profile can widen the sandbox arbitrarily — it
    is checked-in project configuration and is trusted as such, exactly
    like ``.agent-loop/Dockerfile.sandbox`` is for the Docker backends.

    Returns the profile name to extend, or None if the project has none.
    """
    source = project_profile_source(project_dir)
    if not os.path.isfile(source):
        return None
    with open(source, "rb") as f:
        content = f.read()
    name = os.path.basename(dest_path).removesuffix(".json")
    try:
        profile = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"ralph: {source} is not valid JSON: {exc}") from exc
    if not isinstance(profile, dict):
        raise ValueError(f"ralph: {source} must contain a JSON object")
    meta = profile.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        profile["meta"] = meta
    meta["name"] = name
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    with open(dest_path, "w") as f:
        json.dump(profile, f, indent=2)
        f.write("\n")
    return name
