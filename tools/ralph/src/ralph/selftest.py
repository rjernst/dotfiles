"""Selftest orchestration — smoke tests the full pipeline."""

import os
import subprocess
import time

from ralph.agents import get_agent
from ralph.docker_proxy import (
    DOCKER_PROXY_PORT, docker_proxy_health_check, ensure_docker_proxy,
    stop_docker_proxy,
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
from ralph.runtime.tart import TartRuntime
from ralph.token import MS_PER_DAY, read_token_from_keychain


class _SelftestAbort(Exception):
    """Raised to abort selftest early while preserving cleanup."""
    pass


def selftest(agent, dotfiles_dir, runtime_type="docker-sandbox",
             auth_mode=None):
    """Run a full pipeline smoke test without executing a real spec.

    Args:
        agent: agent name (e.g. "claude")
        dotfiles_dir: path to the dotfiles repository
        runtime_type: "docker-sandbox", "docker-container", or "tart"
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

    port = proxy_port_for_agent(agent)
    sandbox_name = f"agent-loop-selftest-{agent}"
    checks = []
    proxy_existed_before = proxy_health_check(port)[0]
    proxy_running = False

    def report(name, passed, detail=""):
        status = "PASS" if passed else "FAIL"
        msg = f"  {status}: {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)
        checks.append(passed)

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

    # CLI-form for error messages (api_key -> api-key)
    cli_mode = auth_mode.replace("_", "-") if auth_mode else None

    try:
        # 1. Check token
        print(f"ralph: selftest starting ({runtime_type})...")
        token_data = read_token_from_keychain(agent, auth_mode)
        if token_data is None:
            if cli_mode:
                hint = (f"no {cli_mode} credentials stored for agent {agent}"
                        f" — run: ralph store-token --auth {cli_mode}")
            else:
                hint = f"no token found for agent {agent} — run: ralph store-token"
            report("check token", False, hint)
            print("ralph: selftest aborted — token required for remaining checks")
            return 1

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
            return 1
        if auth_mode == "api_key":
            report("check token", True, "API key stored")
        else:
            remaining_days = int((expires_at - now_ms) / MS_PER_DAY)
            report("check token", True, f"expires in {remaining_days} days")

        # 1b. Check prerequisites
        prereq_errors = runtime.check_prerequisites()
        if prereq_errors:
            for err in prereq_errors:
                report("prerequisites", False, err)
            print("ralph: selftest aborted — prerequisites not met")
            return 1
        report("prerequisites", True, runtime_type)

        # 2. Start proxy and verify health
        ensure_proxy(agent, port, dotfiles_dir, auth_mode)
        proxy_running = True
        healthy, version, _ = proxy_health_check(port)
        report("proxy health", healthy,
               f"http://localhost:{port}/health (v={version})" if healthy
               else "proxy not reachable after start")
        if not healthy:
            print("ralph: selftest aborted — proxy required for remaining checks")
            return 1

        if runtime_type == "tart":
            _selftest_tart(runtime, agent, sandbox_name, port, auth_mode,
                           report)
        elif runtime_type == "docker-container":
            _selftest_docker_container(runtime, agent, sandbox_name, port,
                                       auth_mode, dotfiles_dir, report)
        else:
            _selftest_docker(runtime, agent, sandbox_name, port, auth_mode,
                             report)

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

    # Report summary
    passed = sum(1 for c in checks if c)
    total = len(checks)
    if all(checks):
        print(f"ralph: selftest complete — all {total} checks passed")
        return 0
    else:
        failed = total - passed
        print(f"ralph: selftest complete — {failed}/{total} checks failed")
        return 1


def _selftest_docker(runtime, agent, sandbox_name, port, auth_mode, report):
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
        ["docker", "sandbox", "exec", sandbox_name,
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
                               "haiku")
    env_args = []
    for k, v in env_vars.items():
        env_args.extend(["-e", f"{k}={v}"])
    result = subprocess.run(
        ["docker", "sandbox", "exec"] + env_args + [
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
        ["docker", "sandbox", "exec", sandbox_name,
         "curl", "-s", "--max-time", "5", "https://google.com"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False,
    )
    blocked = result.returncode != 0
    report("network isolation", blocked,
           "outbound blocked" if blocked
           else "outbound NOT blocked — network policy ineffective")


def _selftest_tart(runtime, agent, sandbox_name, port, auth_mode, report):
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
    env_vars = build_proxy_env(auth_mode, proxy_host, port, "haiku")
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
                                auth_mode, dotfiles_dir, report):
    """Docker container runtime selftest checks.

    Tests the docker socket proxy, container creation, proxy reachability,
    Claude auth, and network isolation.
    """
    # 3. Docker socket proxy
    docker_proxy_existed = docker_proxy_health_check(DOCKER_PROXY_PORT)[0]
    try:
        ensure_docker_proxy(DOCKER_PROXY_PORT, dotfiles_dir)
        healthy, version = docker_proxy_health_check(DOCKER_PROXY_PORT)
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
                                 allowed_hosts)
            healthy, version, hosts = network_proxy_health_check(
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
                                   "haiku")
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
