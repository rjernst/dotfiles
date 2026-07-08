"""Selftest orchestration — smoke tests the full pipeline."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from ralph.agents import get_agent
from ralph.docker_proxy import (
    DOCKER_PROXY_PORT, docker_proxy_health_check,
    docker_proxy_socket_health_check, docker_proxy_socket_state_paths,
    ensure_docker_proxy, stop_docker_proxy,
)
from ralph.network_proxy import (
    NETWORK_PROXY_PORT, network_proxy_health_check, ensure_network_proxy,
    stop_network_proxy,
)
from ralph.proxy import (
    build_proxy_env, proxy_port_for_agent, proxy_health_check,
    ensure_proxy, stop_proxy,
)
from ralph.runtime.container import DockerContainerRuntime
from ralph.runtime.docker_sandbox import DockerSandboxRuntime
from ralph.runtime.nono import NonoRuntime
from ralph.runtime.nono_profile import CAPTURE_NAME
from ralph.runtime.tart import TartRuntime
from ralph.token import MS_PER_DAY, read_token_from_keychain

# Branch the throwaway nono selftest sandbox is keyed on.  It goes through
# Runtime.sandbox_name like any other, so `ralph prune-sandboxes` cleans up
# after an interrupted run.
SELFTEST_BRANCH = "selftest"

# Run inside the nono sandbox to prove the ephemeral port range is open:
# bind a loopback listener and connect back to it, as a test suite or a
# Gradle daemon would.
LOOPBACK_PROBE = (
    "import socket\n"
    "srv = socket.socket()\n"
    "srv.bind(('127.0.0.1', 0))\n"
    "srv.listen(1)\n"
    "cli = socket.create_connection(srv.getsockname(), timeout=5)\n"
    "conn, _ = srv.accept()\n"
    "conn.close()\n"
    "cli.close()\n"
    "srv.close()\n"
)


# Run inside the nono sandbox to save the spec the way an editor does:
# write a temp file beside the target, then rename it over the target.
ATOMIC_SAVE_PROBE = (
    "import os, sys\n"
    "path = sys.argv[1]\n"
    "tmp = path + '.tmp'\n"
    "open(tmp, 'w').write('- [x] task\\n')\n"
    "os.replace(tmp, path)\n"
)

SPEC_SAVE_EXPECTED = "- [x] task\n"


class _SelftestAbort(Exception):
    """Raised to abort selftest early while preserving cleanup."""
    pass


def _make_reporter(checks):
    """Return a report(name, passed, detail) that records into checks."""
    def report(name, passed, detail=""):
        status = "PASS" if passed else "FAIL"
        msg = f"  {status}: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        checks.append(passed)
    return report


def _check_token(agent, auth_mode, cli_mode, report):
    """Report on the stored token; return it, or None if unusable.

    Every backend needs a valid credential for the checks that follow —
    the nono runtime included, since its credential capture shells out to
    `ralph get-token` — so the abort message is printed here.
    """
    token_data = read_token_from_keychain(agent, auth_mode)
    if token_data is None:
        if cli_mode:
            hint = (f"no {cli_mode} credentials stored for agent {agent}"
                    f" — run: ralph store-token --auth {cli_mode}")
        else:
            hint = f"no token found for agent {agent} — run: ralph store-token"
        report("check token", False, hint)
        print("ralph: selftest aborted — token required for remaining checks")
        return None

    now_ms = int(time.time() * 1000)
    expires_at = token_data.get("expiresAt", 0)
    if expires_at <= now_ms:
        if cli_mode:
            hint = (f"{cli_mode} credentials expired for agent {agent}"
                    f" — run: ralph store-token --auth {cli_mode}")
        else:
            hint = f"token expired for agent {agent} — run: ralph store-token"
        report("check token", False, hint)
        print("ralph: selftest aborted — valid token required")
        return None

    if auth_mode == "api_key":
        report("check token", True, "API key stored")
    elif auth_mode == "gateway":
        report("check token", True, "gateway token stored")
    else:
        remaining_days = int((expires_at - now_ms) / MS_PER_DAY)
        report("check token", True, f"expires in {remaining_days} days")
    return token_data


def _summarize(checks):
    """Print the check tally and return the process exit code."""
    passed = sum(1 for c in checks if c)
    total = len(checks)
    if all(checks):
        print(f"ralph: selftest complete — all {total} checks passed")
        return 0
    failed = total - passed
    print(f"ralph: selftest complete — {failed}/{total} checks failed")
    return 1


def selftest(agent, dotfiles_dir, runtime_type="docker-sandbox",
             auth_mode=None):
    """Run a full pipeline smoke test without executing a real spec.

    Args:
        agent: agent name (e.g. "claude")
        dotfiles_dir: path to the dotfiles repository
        runtime_type: one of ralph.runtime.RUNTIME_TYPES
        auth_mode: "oauth", "api_key", or None (uses agent's default)

    Returns 0 if all checks pass, 1 if any fail.
    """
    # Resolve auth_mode to a concrete value for multi-mode agents
    agent_config = get_agent(agent)
    if "auth_modes" in agent_config:
        if auth_mode is None:
            auth_mode = agent_config["default_auth_mode"]
        else:
            auth_mode = auth_mode.replace("-", "_")

    # CLI-form for error messages (api_key -> api-key)
    cli_mode = auth_mode.replace("_", "-") if auth_mode else None

    checks = []
    report = _make_reporter(checks)

    if runtime_type == "nono":
        # The nono backend shares none of the proxy lifecycle below: the
        # agent runs on the host and nono injects the credential itself.
        print(f"ralph: selftest starting ({runtime_type})...")
        token_data = _check_token(agent, auth_mode, cli_mode, report)
        if token_data is None:
            return 1
        runtime = NonoRuntime(dotfiles_dir, auth_mode=auth_mode,
                              token_data=token_data)
        try:
            _selftest_nono(runtime, agent, report, token_data)
        except _SelftestAbort:
            pass  # already reported; fall through to the summary
        except subprocess.TimeoutExpired:
            report("timeout", False, "a check timed out")
        return _summarize(checks)

    port = proxy_port_for_agent(agent)
    sandbox_name = f"agent-loop-selftest-{agent}"
    proxy_existed_before = proxy_health_check(port)[0]
    proxy_running = False

    if runtime_type == "tart":
        runtime = TartRuntime(dotfiles_dir, config={
            "base_image": "ghcr.io/cirruslabs/macos-sequoia-xcode:latest",
        })
    elif runtime_type == "docker-container":
        agent_hosts = agent_config["allowed_hosts"]
        runtime = DockerContainerRuntime(
            dotfiles_dir, allowed_hosts=agent_hosts)
    else:
        runtime = DockerSandboxRuntime(dotfiles_dir)

    try:
        # 1. Check token
        print(f"ralph: selftest starting ({runtime_type})...")
        token_data = _check_token(agent, auth_mode, cli_mode, report)
        if token_data is None:
            return 1

        # 1b. Check prerequisites
        prereq_errors = runtime.check_prerequisites()
        if prereq_errors:
            for err in prereq_errors:
                report("prerequisites", False, err)
            print("ralph: selftest aborted — prerequisites not met")
            return 1
        report("prerequisites", True, runtime_type)

        # 2. Start proxy and verify health
        ensure_proxy(agent, port, dotfiles_dir, auth_mode,
                     runtime.proxy_listen_addr())
        proxy_running = True
        healthy, version, _, _ = proxy_health_check(port)
        report("proxy health", healthy,
               f"http://localhost:{port}/health (v={version})" if healthy
               else "proxy not reachable after start")
        if not healthy:
            print("ralph: selftest aborted — proxy required for remaining checks")
            return 1

        if runtime_type == "tart":
            _selftest_tart(runtime, agent, sandbox_name, port, auth_mode,
                           report, token_data)
        elif runtime_type == "docker-container":
            _selftest_docker_container(runtime, agent, sandbox_name, port,
                                       auth_mode, dotfiles_dir, report,
                                       token_data)
        else:
            _selftest_docker(runtime, agent, sandbox_name, port, auth_mode,
                             report, token_data)

    except _SelftestAbort:
        pass  # already reported; fall through to cleanup + summary
    except subprocess.TimeoutExpired:
        report("timeout", False, "a check timed out")
    finally:
        # Always attempt cleanup — remove_sandbox is best-effort/idempotent
        print(f"ralph: cleaning up test sandbox {sandbox_name}...")
        runtime.remove_sandbox(sandbox_name)
        if proxy_running and not proxy_existed_before:
            stop_proxy(agent)

    return _summarize(checks)


def _selftest_docker(runtime, agent, sandbox_name, port, auth_mode, report,
                     token_data=None):
    """Docker-specific selftest checks."""
    # 3. Build/ensure image
    try:
        tag = runtime.ensure_image(agent)
        report("build image", True, tag)
    except Exception as e:
        report("build image", False, str(e))
        print("ralph: selftest aborted — image required for remaining checks")
        raise _SelftestAbort()

    # 3b. Build project image (if project config exists in cwd)
    project_config = DockerSandboxRuntime.find_project_config(os.getcwd())
    if project_config is not None:
        try:
            tag = runtime.ensure_project_image(
                agent, tag, os.getcwd())
            report("build project image", True, tag)
        except Exception as e:
            report("build project image", False, str(e))
            print("ralph: selftest aborted — project image "
                  "required for remaining checks")
            raise _SelftestAbort()
    else:
        print("ralph: no .agent-loop/ config in cwd, "
              "skipping project image check")

    # 4. Create test sandbox
    try:
        runtime.remove_sandbox(sandbox_name)
        runtime._ensure_global_policy()
        runtime._ensure_template_loaded(tag)
        git_common_dir = DockerSandboxRuntime._resolve_git_common_dir(os.getcwd())
        agent_config = get_agent(agent)
        runtime._docker_sandbox_create(sandbox_name, tag, os.getcwd(),
                                       git_common_dir,
                                       sandbox_agent=agent_config["sandbox_agent"])
        report("create sandbox", True, sandbox_name)
    except Exception as e:
        report("create sandbox", False, str(e))
        print("ralph: selftest aborted — sandbox required for remaining checks")
        raise _SelftestAbort()

    # 5. Apply network policy
    try:
        runtime.apply_network_policy(sandbox_name,
                                       agent_config["allowed_hosts"])
        report("network policy", True)
    except Exception as e:
        report("network policy", False, str(e))

    # 6. Verify proxy reachable from sandbox
    result = subprocess.run(
        ["sbx", "exec", sandbox_name,
         "curl", "-sf", "--max-time", "5",
         f"http://host.docker.internal:{port}/health"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False,
    )
    report("proxy reachable from sandbox", result.returncode == 0,
           "via host.docker.internal" if result.returncode == 0
           else f"curl exit code {result.returncode}")

    # 7. Verify Claude auth works through proxy
    env_vars = build_proxy_env(auth_mode, "host.docker.internal", port,
                               "haiku", token_data)
    env_args = []
    for k, v in env_vars.items():
        env_args.extend(["-e", f"{k}={v}"])
    result = subprocess.run(
        ["sbx", "exec"] + env_args + [
         sandbox_name,
         "claude", "-p", "say ok", "--model", "haiku"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False, timeout=60,
    )
    report("claude auth via proxy", result.returncode == 0,
           "response received" if result.returncode == 0
           else f"exit code {result.returncode}")

    # 8. Verify network isolation (google.com should be blocked).
    # Use -sf so curl exits non-zero on HTTP 4xx responses (sbx gateway blocks
    # via MITM 403, not at the TCP level, so -f is required to detect blocking).
    result = subprocess.run(
        ["sbx", "exec", sandbox_name,
         "curl", "-sf", "--max-time", "5", "https://google.com"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False,
    )
    blocked = result.returncode != 0
    report("network isolation", blocked,
           "outbound blocked" if blocked
           else "outbound NOT blocked — network policy ineffective")


def _selftest_tart(runtime, agent, sandbox_name, port, auth_mode, report,
                   token_data=None):
    """Tart-specific selftest checks."""
    # 3. Build template
    try:
        template = runtime.ensure_image(agent)
        report("build template", True, template)
    except Exception as e:
        report("build template", False, str(e))
        print("ralph: selftest aborted — template required for remaining checks")
        raise _SelftestAbort()

    # 4. Create test VM
    try:
        runtime.remove_sandbox(sandbox_name)
        # Clone template and start VM
        subprocess.run(
            ["tart", "clone", template, sandbox_name],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        proc = subprocess.Popen(
            ["tart", "run", sandbox_name, "--no-graphics"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        runtime._vm_procs[sandbox_name] = proc
        report("create test VM", True, sandbox_name)
    except Exception as e:
        report("create test VM", False, str(e))
        print("ralph: selftest aborted — VM required for remaining checks")
        raise _SelftestAbort()

    # 5. Verify tart exec works (wait for guest agent)
    try:
        runtime._wait_for_guest_agent(sandbox_name)
        report("tart exec", True, "guest agent responsive")
    except Exception as e:
        report("tart exec", False, str(e))
        print("ralph: selftest aborted — guest agent required for remaining checks")
        raise _SelftestAbort()

    # 6. Verify proxy reachable from VM via host IP
    proxy_host = runtime.proxy_host()
    result = subprocess.run(
        ["tart", "exec", sandbox_name,
         "curl", "-sf", "--max-time", "5",
         f"http://{proxy_host}:{port}/health"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False,
    )
    report("proxy reachable from VM", result.returncode == 0,
           f"via {proxy_host}" if result.returncode == 0
           else f"curl exit code {result.returncode}")

    # 7. Verify Claude auth via proxy
    env_vars = build_proxy_env(auth_mode, proxy_host, port, "haiku", token_data)
    env_prefix = " ".join(f"{k}={v}" for k, v in env_vars.items())
    claude_cmd = f"{env_prefix} claude -p 'say ok' --model haiku"
    result = subprocess.run(
        ["tart", "exec", sandbox_name, "bash", "-c", claude_cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False, timeout=60,
    )
    report("claude auth via proxy", result.returncode == 0,
           "response received" if result.returncode == 0
           else f"exit code {result.returncode}")

    # 8. Note: no network isolation for Tart VMs
    print("ralph: note: Tart VMs do not have network isolation — skipping check")


def _selftest_docker_container(runtime, agent, sandbox_name, port,
                                auth_mode, dotfiles_dir, report,
                                token_data=None):
    """Docker container runtime selftest checks.

    Tests the docker socket proxy, container creation, proxy reachability,
    Claude auth, and network isolation.
    """
    # 3. Docker socket proxy
    docker_proxy_existed = docker_proxy_health_check(DOCKER_PROXY_PORT)[0]
    try:
        ensure_docker_proxy(DOCKER_PROXY_PORT, dotfiles_dir,
                            runtime.proxy_listen_addr())
        healthy, version, _ = docker_proxy_health_check(DOCKER_PROXY_PORT)
        report("docker socket proxy", healthy,
               f"http://localhost:{DOCKER_PROXY_PORT}/health (v={version})"
               if healthy else "proxy not reachable after start")
        if not healthy:
            print("ralph: selftest aborted — docker socket proxy required")
            raise _SelftestAbort()
    except _SelftestAbort:
        raise
    except Exception as e:
        report("docker socket proxy", False, str(e))
        print("ralph: selftest aborted — docker socket proxy required")
        raise _SelftestAbort()

    # 3b. Network proxy (if allowed_hosts configured)
    agent_config = get_agent(agent)
    allowed_hosts = agent_config["allowed_hosts"]
    network_proxy_existed = network_proxy_health_check(NETWORK_PROXY_PORT)[0]
    if allowed_hosts:
        try:
            ensure_network_proxy(NETWORK_PROXY_PORT, dotfiles_dir,
                                 allowed_hosts, runtime.proxy_listen_addr())
            healthy, version, hosts, _ = network_proxy_health_check(
                NETWORK_PROXY_PORT)
            report("network proxy", healthy,
                   f"http://localhost:{NETWORK_PROXY_PORT}/health (v={version})"
                   if healthy else "proxy not reachable after start")
            if not healthy:
                print("ralph: selftest aborted — network proxy required")
                raise _SelftestAbort()
        except _SelftestAbort:
            raise
        except Exception as e:
            report("network proxy", False, str(e))
            print("ralph: selftest aborted — network proxy required")
            raise _SelftestAbort()

    try:
        # 4. Build/ensure image
        try:
            tag = runtime.ensure_image(agent)
            report("build image", True, tag)
        except Exception as e:
            report("build image", False, str(e))
            print("ralph: selftest aborted — image required for remaining checks")
            raise _SelftestAbort()

        # 4b. Build project image (if project config exists in cwd)
        project_config = DockerContainerRuntime.find_project_config(os.getcwd())
        if project_config is not None:
            try:
                tag = runtime.ensure_project_image(
                    agent, tag, os.getcwd())
                report("build project image", True, tag)
            except Exception as e:
                report("build project image", False, str(e))
                print("ralph: selftest aborted — project image "
                      "required for remaining checks")
                raise _SelftestAbort()
        else:
            print("ralph: no .agent-loop/ config in cwd, "
                  "skipping project image check")

        # 5. Create test container
        try:
            runtime.remove_sandbox(sandbox_name)
            runtime._ensure_network()
            git_common_dir = DockerContainerRuntime._resolve_git_common_dir(
                os.getcwd())
            cmd = [
                "docker", "run", "-d",
                "--name", sandbox_name,
                "--network", "ralph-agent-loop",
                "-v", f"{os.getcwd()}:{os.getcwd()}",
            ]
            if git_common_dir:
                cmd.extend(["-v", f"{git_common_dir}:{git_common_dir}"])
            cmd.extend([
                "-e",
                f"DOCKER_HOST=tcp://host.docker.internal:{DOCKER_PROXY_PORT}",
            ])
            if allowed_hosts:
                proxy_url = (
                    f"http://host.docker.internal:{NETWORK_PROXY_PORT}")
                cmd.extend([
                    "-e", f"HTTP_PROXY={proxy_url}",
                    "-e", f"HTTPS_PROXY={proxy_url}",
                    "-e", "NO_PROXY=host.docker.internal",
                ])
            cmd.extend([tag, "sleep", "infinity"])
            subprocess.run(cmd, check=True)
            report("create container", True, sandbox_name)
        except Exception as e:
            report("create container", False, str(e))
            print("ralph: selftest aborted "
                  "— container required for remaining checks")
            raise _SelftestAbort()

        # 6. Verify credential proxy reachable from container
        result = subprocess.run(
            ["docker", "exec", sandbox_name,
             "curl", "-sf", "--max-time", "5",
             f"http://host.docker.internal:{port}/health"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        report("proxy reachable from container", result.returncode == 0,
               "via host.docker.internal" if result.returncode == 0
               else f"curl exit code {result.returncode}")

        # 7. Verify Claude auth works through proxy
        env_vars = build_proxy_env(auth_mode, "host.docker.internal", port,
                                   "haiku", token_data)
        env_args = []
        for k, v in env_vars.items():
            env_args.extend(["-e", f"{k}={v}"])
        result = subprocess.run(
            ["docker", "exec"] + env_args + [
             sandbox_name,
             "claude", "-p", "say ok", "--model", "haiku"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=60,
        )
        report("claude auth via proxy", result.returncode == 0,
               "response received" if result.returncode == 0
               else f"exit code {result.returncode}")

        # 8. Verify network isolation (google.com should be blocked)
        result = subprocess.run(
            ["docker", "exec", sandbox_name,
             "curl", "-s", "--max-time", "5", "https://google.com"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        blocked = result.returncode != 0
        report("network isolation", blocked,
               "outbound blocked" if blocked
               else "outbound NOT blocked — network policy ineffective")

        # 9. Verify docker socket proxy reachable from container
        result = subprocess.run(
            ["docker", "exec", sandbox_name,
             "curl", "-sf", "--max-time", "5",
             f"http://host.docker.internal:{DOCKER_PROXY_PORT}/health"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False,
        )
        report("docker socket proxy from container", result.returncode == 0,
               "via host.docker.internal" if result.returncode == 0
               else f"curl exit code {result.returncode}")

        # 10. Verify network proxy reachable from container (if configured)
        if allowed_hosts:
            result = subprocess.run(
                ["docker", "exec", sandbox_name,
                 "curl", "-sf", "--max-time", "5",
                 f"http://host.docker.internal:{NETWORK_PROXY_PORT}/health"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            report("network proxy from container", result.returncode == 0,
                   "via host.docker.internal" if result.returncode == 0
                   else f"curl exit code {result.returncode}")

            # 11. Verify allowed host is accessible via network proxy
            test_host = allowed_hosts[0]
            result = subprocess.run(
                ["docker", "exec", sandbox_name,
                 "curl", "-sf", "--max-time", "5",
                 f"https://{test_host}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            report("allowed host via proxy", result.returncode == 0,
                   f"{test_host} reachable" if result.returncode == 0
                   else f"{test_host} not reachable (exit {result.returncode})")

            # 12. Verify non-allowed host is blocked via network proxy
            result = subprocess.run(
                ["docker", "exec", sandbox_name,
                 "curl", "-sf", "--max-time", "5",
                 "https://example.com"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False,
            )
            blocked = result.returncode != 0
            report("non-allowed host blocked", blocked,
                   "example.com blocked" if blocked
                   else "example.com NOT blocked — proxy filtering ineffective")

    finally:
        if not docker_proxy_existed:
            stop_docker_proxy()
        if allowed_hosts and not network_proxy_existed:
            stop_network_proxy()


# ---------------------------------------------------------------------------
# nono
# ---------------------------------------------------------------------------

def _first_line(text):
    """First non-empty line of a command's output, for a report detail."""
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _parse_env(text):
    """Parse `env` output into a dict.

    First occurrence wins, so a continuation line of a multi-line value
    cannot shadow a real variable.
    """
    result = {}
    for line in (text or "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result.setdefault(key, value)
    return result


def _git(args, **kwargs):
    """Run a host git command quietly."""
    return subprocess.run(
        ["git"] + args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, **kwargs)


def _init_selftest_repo(path):
    """Create a throwaway repo with a linked worktree, as ralph uses.

    The worktree is what makes this worth doing: the generated profile
    grants the per-worktree git dir and keeps the common dir read-only,
    a split a plain checkout does not have.

    Returns (repo, worktree), the worktree holding one uncommitted file.
    """
    repo = os.path.join(path, "repo")
    worktree = os.path.join(path, "worktree")
    identity = ["-c", "user.name=Ralph Selftest",
                "-c", "user.email=ralph-selftest@example.invalid",
                "-c", "commit.gpgsign=false"]
    os.makedirs(repo, exist_ok=True)
    _git(["init", "-q", "-b", "main", repo], check=True)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("ralph nono selftest\n")
    _git(["-C", repo, "add", "README.md"], check=True)
    _git(["-C", repo] + identity + ["commit", "-q", "-m", "initial"],
         check=True)
    _git(["-C", repo, "worktree", "add", "-q", "-b", SELFTEST_BRANCH,
          worktree], check=True)
    # git created the worktree; exist_ok keeps the write simple either way.
    os.makedirs(worktree, exist_ok=True)
    with open(os.path.join(worktree, "spec-work.md"), "w") as f:
        f.write("ralph nono selftest\n")
    return repo, worktree


def _selftest_nono(runtime, agent, report, token_data):
    """nono-specific selftest checks.

    Every check runs through the same generated profile an iteration would
    get, against a throwaway git repo outside any real project.  The state
    directory, the repo, and a Docker socket proxy this run started are all
    cleaned up afterwards.
    """
    # 1. Prerequisites and version
    prereq_errors = runtime.check_prerequisites()
    if prereq_errors:
        for err in prereq_errors:
            report("prerequisites", False, err)
        print("ralph: selftest aborted — prerequisites not met")
        raise _SelftestAbort()
    version = runtime._nono_version()
    report("prerequisites", True,
           ("nono " + ".".join(str(part) for part in version)) if version
           else "nono")

    sandbox_name = runtime.sandbox_name(agent, SELFTEST_BRANCH)
    docker_socket = runtime.docker_socket_path()
    docker_proxy_existed = docker_proxy_socket_health_check(docker_socket)[0]
    # Probe inside ~/.claude when it exists, else directly in $HOME: a write
    # into a missing directory fails with ENOENT and would prove nothing.
    claude_dir = os.path.join(runtime.home, ".claude")
    if os.path.isdir(claude_dir):
        claude_label, claude_probe = "~/.claude", os.path.join(
            claude_dir, "ralph-selftest-probe")
    else:
        claude_label, claude_probe = "~", os.path.join(
            runtime.home, ".ralph-selftest-probe")
    gitconfig = os.path.join(runtime.home, ".gitconfig")
    gitconfig_existed = os.path.exists(gitconfig)
    # realpath so the grant matches the path git resolves on macOS, where
    # /var/folders/... is a symlink into /private.
    scratch = os.path.realpath(tempfile.mkdtemp(prefix="ralph-selftest-"))
    worktree = None
    try:
        # 2. Sandbox state, gitconfig, and generated profile
        try:
            repo, worktree = _init_selftest_repo(scratch)
            # Sandbox state lives in the repo's git dir, so the runtime is
            # pointed at the throwaway repo before anything is written —
            # and all of it goes when that repo does.
            runtime.project_dir = repo
            runtime.ensure_sandbox(agent, SELFTEST_BRANCH, worktree,
                                   project_dir=repo)
            runtime.setup_git_config(sandbox_name, "Ralph Selftest",
                                     "ralph-selftest@example.invalid")
            profile_path = runtime._build_profile(sandbox_name, agent=agent)
            with open(profile_path) as f:
                profile = json.load(f)
            env = runtime._iteration_env(sandbox_name)
        except Exception as e:
            report("create sandbox", False, str(e))
            print("ralph: selftest aborted — sandbox required for remaining checks")
            raise _SelftestAbort()
        report("create sandbox", True, sandbox_name)

        def in_sandbox(command, timeout=30):
            """Run a command under the generated profile."""
            return subprocess.run(
                runtime._nono_run_command(profile_path, worktree, command),
                cwd=worktree, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, check=False,
                timeout=timeout,
            )

        # 3. nono accepts the profile as a file path
        result = in_sandbox(["true"])
        accepted = result.returncode == 0
        report("nono profile accepted", accepted,
               profile_path if accepted
               else _first_line(result.stderr)
               or f"exit code {result.returncode}")
        if not accepted:
            # Every remaining check runs through this profile; without it
            # they would fail — or pass — for the wrong reason.
            print("ralph: selftest aborted — profile required for remaining checks")
            raise _SelftestAbort()

        # 4. The capture command nono runs host-side to fetch the token
        capture = profile["credential_capture"][CAPTURE_NAME]["command"]
        captured = subprocess.run(
            capture, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, check=False, timeout=30,
        )
        report("credential capture command", captured.returncode == 0,
               shlex.join(capture) if captured.returncode == 0
               else _first_line(captured.stderr)
               or f"exit code {captured.returncode}")

        # 5. The agent talks to nono's per-session proxy on loopback
        sandbox_env = _parse_env(in_sandbox(["env"]).stdout)
        base_url = sandbox_env.get("ANTHROPIC_BASE_URL", "")
        report("credential proxy env", "127.0.0.1" in base_url,
               base_url or "ANTHROPIC_BASE_URL not set in sandbox")

        # 6. ...and never sees the real credential
        token_var = (profile["network"]["custom_credentials"]["anthropic"]
                     ["env_var"])
        injected = sandbox_env.get(token_var)
        real = {token_data.get("accessToken")}
        if captured.returncode == 0:
            real.add(captured.stdout.strip())
        real.discard(None)
        real.discard("")
        hidden = bool(injected) and injected not in real
        if hidden:
            detail = f"{token_var} holds a placeholder"
        elif not injected:
            detail = f"{token_var} not set in sandbox"
        else:
            detail = f"{token_var} holds the real token"
        report("real token hidden from sandbox", hidden, detail)

        # 7. Credential injection end to end
        result = in_sandbox(
            ["claude", "-p", "reply with OK", "--model", "haiku"], timeout=60)
        report("claude auth via nono", result.returncode == 0,
               "response received" if result.returncode == 0
               else _first_line(result.stderr)
               or f"exit code {result.returncode}")

        # 8. Filtered mode blocks anything off the allowlist
        if runtime.network == "filtered":
            result = in_sandbox(
                ["curl", "-s", "--max-time", "5", "https://google.com"])
            blocked = result.returncode != 0
            report("network isolation", blocked,
                   "outbound blocked" if blocked
                   else "outbound NOT blocked — network filter ineffective")
        else:
            print("ralph: note: network mode is unrestricted "
                  "— skipping network isolation check")

        # 9. Loopback ephemeral ports stay open for tests and build daemons
        result = in_sandbox([sys.executable, "-c", LOOPBACK_PROBE])
        report("loopback ports", result.returncode == 0,
               "bound and connected on 127.0.0.1" if result.returncode == 0
               else _first_line(result.stderr)
               or f"exit code {result.returncode}")

        # 10. The token store is out of reach
        result = in_sandbox(runtime._token_store_read_command(agent))
        denied = result.returncode != 0
        report("token store denied", denied,
               "token store not readable" if denied
               else "token store IS readable — the agent can read the token")

        # 11. Docker reaches the daemon only through the filtered socket
        result = in_sandbox(["docker", "version"])
        report("docker via socket proxy", result.returncode == 0,
               f"unix:{docker_socket}" if result.returncode == 0
               else _first_line(result.stderr)
               or f"exit code {result.returncode}")

        # 12. The user's own Claude and git config stay read-only
        writable = []
        rc = in_sandbox(["sh", "-c", f": >> {shlex.quote(claude_probe)}"]).returncode
        if rc == 0 or os.path.exists(claude_probe):
            writable.append(claude_label)
        # ':' writes nothing, so an ineffective sandbox still leaves the
        # real ~/.gitconfig byte-for-byte unchanged.
        rc = in_sandbox(["sh", "-c", f": >> {shlex.quote(gitconfig)}"]).returncode
        if rc == 0:
            writable.append("~/.gitconfig")
        report("home config write denied", not writable,
               f"{claude_label} and ~/.gitconfig not writable" if not writable
               else "writable from the sandbox: " + ", ".join(writable))

        # 13. git hooks and config stay read-only: git runs both on the
        #     host, outside the sandbox, so writing them would be an escape
        git_common_dir = runtime._resolve_git_common_dir(worktree)
        hook_probe = os.path.join(git_common_dir, "hooks", "ralph-selftest-probe")
        writable = []
        rc = in_sandbox(["sh", "-c", f": >> {shlex.quote(hook_probe)}"]).returncode
        if rc == 0 or os.path.exists(hook_probe):
            writable.append(".git/hooks")
        rc = in_sandbox(
            ["sh", "-c",
             f": >> {shlex.quote(os.path.join(git_common_dir, 'config'))}"],
        ).returncode
        if rc == 0:
            writable.append(".git/config")
        report("git hooks and config denied", not writable,
               "not writable from the sandbox" if not writable
               else "writable from the sandbox: " + ", ".join(writable))

        # 14. Commits land in the host worktree with the generated gitconfig
        result = in_sandbox(
            ["sh", "-c", "git add -A && git commit -q -m 'ralph nono selftest'"])
        head = _git(["-C", worktree, "rev-parse", "--verify", "HEAD"])
        committed = result.returncode == 0 and head.returncode == 0
        report("git commit in worktree", committed,
               head.stdout.strip()[:12] if committed
               else _first_line(result.stderr)
               or f"exit code {result.returncode}")

        # 15. ...unsigned: the sandbox has no key, and a commit that claimed
        #     the user's signature would be worse than one without
        signature = _git(["-C", worktree, "log", "-1", "--format=%G?"])
        flag = signature.stdout.strip()
        report("commit is unsigned", committed and flag == "N",
               "no signature" if flag == "N"
               else f"git reports signature status {flag!r}")

        # 16. The agent can save the spec back.  Claude Code writes files
        #     atomically (temp file beside the target, then rename), which a
        #     single-file grant does not survive — hence a whole spec dir.
        spec_dir = tempfile.mkdtemp(prefix="spec-",
                                    dir=runtime.state_dir(sandbox_name))
        spec_path = os.path.join(spec_dir, "spec.md")
        with open(spec_path, "w") as f:
            f.write("- [ ] task\n")
        spec_profile = runtime._build_profile(sandbox_name, agent=agent,
                                              spec_dir=spec_dir)
        result = subprocess.run(
            runtime._nono_run_command(
                spec_profile, worktree,
                [sys.executable, "-c", ATOMIC_SAVE_PROBE, spec_path]),
            cwd=worktree, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, check=False, timeout=30,
        )
        try:
            with open(spec_path) as f:
                saved = f.read()
        except OSError:
            saved = ""
        report("spec editable by the agent", saved == SPEC_SAVE_EXPECTED,
               "atomic save landed" if saved == SPEC_SAVE_EXPECTED
               else _first_line(result.stderr)
               or "the agent could not save the spec")
    finally:
        print(f"ralph: cleaning up test sandbox {sandbox_name}...")
        # Only once the throwaway repo exists: before that the sandbox has
        # no state dir to resolve, and resolving one would point at
        # whatever repo ralph happens to be running in.
        if worktree:
            runtime.remove_sandbox(sandbox_name)
        shutil.rmtree(scratch, ignore_errors=True)
        try:
            os.unlink(claude_probe)
        except OSError:
            pass
        # An ineffective sandbox creates an empty ~/.gitconfig; drop it
        # again so the probe leaves the host exactly as it found it.
        if not gitconfig_existed:
            try:
                if os.path.getsize(gitconfig) == 0:
                    os.unlink(gitconfig)
            except OSError:
                pass
        if not docker_proxy_existed:
            pid_file = docker_proxy_socket_state_paths(docker_socket)[0]
            stop_docker_proxy(pid_file=pid_file)
