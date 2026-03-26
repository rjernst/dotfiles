"""Selftest orchestration — smoke tests the full pipeline."""

import os
import subprocess
import time

from ralph.proxy import (
    MODEL_ALIASES, proxy_port_for_agent, proxy_health_check,
    ensure_proxy, stop_proxy,
)
from ralph.sandbox.docker import DockerSandbox
from ralph.sandbox.tart import TartSandbox
from ralph.token import MS_PER_DAY, read_token_from_keychain


class _SelftestAbort(Exception):
    """Raised to abort selftest early while preserving cleanup."""
    pass


def selftest(agent, dotfiles_dir, sandbox_type="docker"):
    """Run a full pipeline smoke test without executing a real spec.

    Args:
        agent: agent name (e.g. "claude")
        dotfiles_dir: path to the dotfiles repository
        sandbox_type: "docker" or "tart"

    Returns 0 if all checks pass, 1 if any fail.
    """
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

    if sandbox_type == "tart":
        sandbox = TartSandbox(dotfiles_dir, config={
            "base_image": "ghcr.io/cirruslabs/macos-sequoia-xcode:latest",
        })
    else:
        sandbox = DockerSandbox(dotfiles_dir)

    try:
        # 1. Check token
        print(f"ralph: selftest starting ({sandbox_type})...")
        token_data = read_token_from_keychain(agent)
        if token_data is None:
            report("check token", False, "no token found — run: ralph store-token")
            print("ralph: selftest aborted — token required for remaining checks")
            return 1

        now_ms = int(time.time() * 1000)
        expires_at = token_data.get("expiresAt", 0)
        if expires_at <= now_ms:
            report("check token", False, "token expired — run: ralph store-token")
            print("ralph: selftest aborted — valid token required")
            return 1
        remaining_days = int((expires_at - now_ms) / MS_PER_DAY)
        report("check token", True, f"expires in {remaining_days} days")

        # 1b. Check prerequisites
        prereq_errors = sandbox.check_prerequisites()
        if prereq_errors:
            for err in prereq_errors:
                report("prerequisites", False, err)
            print("ralph: selftest aborted — prerequisites not met")
            return 1
        report("prerequisites", True, sandbox_type)

        # 2. Start proxy and verify health
        ensure_proxy(agent, port, dotfiles_dir)
        proxy_running = True
        healthy, version = proxy_health_check(port)
        report("proxy health", healthy,
               f"http://localhost:{port}/health (v={version})" if healthy
               else "proxy not reachable after start")
        if not healthy:
            print("ralph: selftest aborted — proxy required for remaining checks")
            return 1

        if sandbox_type == "tart":
            _selftest_tart(sandbox, agent, sandbox_name, port, report)
        else:
            _selftest_docker(sandbox, agent, sandbox_name, port, report)

    except _SelftestAbort:
        pass  # already reported; fall through to cleanup + summary
    except subprocess.TimeoutExpired:
        report("timeout", False, "a check timed out")
    finally:
        # Always attempt cleanup — remove_sandbox is best-effort/idempotent
        print(f"ralph: cleaning up test sandbox {sandbox_name}...")
        sandbox.remove_sandbox(sandbox_name)
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


def _selftest_docker(sandbox, agent, sandbox_name, port, report):
    """Docker-specific selftest checks."""
    # 3. Build/ensure image
    try:
        tag = sandbox.ensure_image(agent)
        report("build image", True, tag)
    except Exception as e:
        report("build image", False, str(e))
        print("ralph: selftest aborted — image required for remaining checks")
        raise _SelftestAbort()

    # 3b. Build project image (if project config exists in cwd)
    project_config = DockerSandbox.find_project_config(os.getcwd())
    if project_config is not None:
        try:
            tag = sandbox.ensure_project_image(
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
        sandbox.remove_sandbox(sandbox_name)
        git_common_dir = DockerSandbox._resolve_git_common_dir(os.getcwd())
        sandbox._docker_sandbox_create(sandbox_name, tag, os.getcwd(),
                                       git_common_dir)
        report("create sandbox", True, sandbox_name)
    except Exception as e:
        report("create sandbox", False, str(e))
        print("ralph: selftest aborted — sandbox required for remaining checks")
        raise _SelftestAbort()

    # 5. Apply network policy
    try:
        DockerSandbox.apply_network_policy(sandbox_name)
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
    result = subprocess.run(
        ["docker", "sandbox", "exec",
         "-e", "CLAUDE_CODE_OAUTH_TOKEN=phantom",
         "-e", f"ANTHROPIC_BASE_URL=http://host.docker.internal:{port}",
         "-e", f"ANTHROPIC_CUSTOM_MODEL_OPTION={MODEL_ALIASES['haiku']}",
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


def _selftest_tart(sandbox, agent, sandbox_name, port, report):
    """Tart-specific selftest checks."""
    # 3. Build template
    try:
        template = sandbox.ensure_image(agent)
        report("build template", True, template)
    except Exception as e:
        report("build template", False, str(e))
        print("ralph: selftest aborted — template required for remaining checks")
        raise _SelftestAbort()

    # 4. Create test VM
    try:
        sandbox.remove_sandbox(sandbox_name)
        # Clone template and start VM
        subprocess.run(
            ["tart", "clone", template, sandbox_name],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        proc = subprocess.Popen(
            ["tart", "run", sandbox_name, "--no-graphics"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        sandbox._vm_procs[sandbox_name] = proc
        report("create test VM", True, sandbox_name)
    except Exception as e:
        report("create test VM", False, str(e))
        print("ralph: selftest aborted — VM required for remaining checks")
        raise _SelftestAbort()

    # 5. Verify tart exec works (wait for guest agent)
    try:
        sandbox._wait_for_guest_agent(sandbox_name)
        report("tart exec", True, "guest agent responsive")
    except Exception as e:
        report("tart exec", False, str(e))
        print("ralph: selftest aborted — guest agent required for remaining checks")
        raise _SelftestAbort()

    # 6. Verify proxy reachable from VM via host IP
    proxy_host = sandbox.proxy_host()
    result = subprocess.run(
        ["tart", "exec", sandbox_name, "--",
         "curl", "-sf", "--max-time", "5",
         f"http://{proxy_host}:{port}/health"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False,
    )
    report("proxy reachable from VM", result.returncode == 0,
           f"via {proxy_host}" if result.returncode == 0
           else f"curl exit code {result.returncode}")

    # 7. Verify Claude auth via proxy
    proxy_url = f"http://{proxy_host}:{port}"
    claude_cmd = (
        f"CLAUDE_CODE_OAUTH_TOKEN=phantom"
        f" ANTHROPIC_BASE_URL={proxy_url}"
        f" ANTHROPIC_CUSTOM_MODEL_OPTION={MODEL_ALIASES['haiku']}"
        f" claude -p 'say ok' --model haiku"
    )
    result = subprocess.run(
        ["tart", "exec", sandbox_name, "--", "bash", "-c", claude_cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        check=False, timeout=60,
    )
    report("claude auth via proxy", result.returncode == 0,
           "response received" if result.returncode == 0
           else f"exit code {result.returncode}")

    # 8. Note: no network isolation for Tart VMs
    print("ralph: note: Tart VMs do not have network isolation — skipping check")
