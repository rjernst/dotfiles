"""Integration tests for ralph — exercises the full sandbox pipeline.

These tests require Docker Desktop running. Some tests additionally
require a valid token stored in Keychain (proxy/auth tests for claude).

Enable with:
    RALPH_INTEGRATION_TESTS=1 pytest -m integration tools/ralph/tests/test_integration.py -v
"""

import os
import subprocess
import time

import pytest

from dotlib import DOTFILES_DIR
from ralph.agents import VALID_AGENTS, get_agent
from ralph.proxy import proxy_port_for_agent, ensure_proxy, stop_proxy
from ralph.selftest import selftest
from ralph.sandbox.docker import DockerSandbox
from ralph.token import read_token_from_keychain

# Skip entire module unless integration tests are enabled
pytestmark = pytest.mark.integration

REPO_ROOT = DOTFILES_DIR


def _docker_available():
    """Check if Docker is available and running."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _token_available(agent):
    """Check if a valid token is stored in Keychain."""
    data = read_token_from_keychain(agent)
    if data is None:
        return False
    now_ms = int(time.time() * 1000)
    return data.get("expiresAt", 0) > now_ms


skip_unless_integration = pytest.mark.skipif(
    os.environ.get("RALPH_INTEGRATION_TESTS") != "1",
    reason="Set RALPH_INTEGRATION_TESTS=1 to run integration tests",
)


# ---------------------------------------------------------------------------
# Infrastructure tests — parameterized across all agents
# ---------------------------------------------------------------------------

@skip_unless_integration
class TestSandboxInfrastructure:
    """Infrastructure tests that run for every agent.

    These verify the Docker plumbing (image build, sandbox create, network
    policy, exec, isolation) without needing a real API key or the agent CLI.
    """

    @pytest.fixture(autouse=True)
    def _check_docker(self):
        if not _docker_available():
            pytest.skip("Docker is not available or not running")

    @pytest.fixture(params=VALID_AGENTS)
    def agent(self, request):
        return request.param

    @pytest.fixture
    def sandbox_image(self, agent):
        sandbox = DockerSandbox(REPO_ROOT)
        return sandbox.ensure_image(agent)

    @pytest.fixture
    def test_sandbox(self, agent, sandbox_image):
        agent_config = get_agent(agent)
        sandbox = DockerSandbox(REPO_ROOT)
        name = f"agent-loop-selftest-{agent}"
        sandbox.remove_sandbox(name)

        git_common_dir = DockerSandbox._resolve_git_common_dir(os.getcwd())
        sandbox._docker_sandbox_create(
            name, sandbox_image, os.getcwd(), git_common_dir,
            sandbox_agent=agent_config["sandbox_agent"])
        DockerSandbox.apply_network_policy(name, agent_config["allowed_hosts"])
        yield name

        sandbox.remove_sandbox(name)

    def test_image_build(self, agent, sandbox_image):
        """Verify that the sandbox image builds successfully."""
        assert sandbox_image.startswith(f"agent-loop-sandbox-{agent}:v")

        result = subprocess.run(
            ["docker", "image", "inspect", sandbox_image],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=120,
        )
        assert result.returncode == 0, f"Image {sandbox_image} not found locally"

    def test_sandbox_create_and_exec(self, test_sandbox):
        """Verify sandbox can be created and responds to exec."""
        result = subprocess.run(
            ["docker", "sandbox", "exec", test_sandbox, "echo", "hello"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=120,
        )
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_network_isolation(self, test_sandbox):
        """Verify outbound network requests to unlisted hosts are blocked."""
        result = subprocess.run(
            ["docker", "sandbox", "exec", test_sandbox,
             "curl", "-s", "--max-time", "5", "https://google.com"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=120,
        )
        assert result.returncode != 0, (
            "Network isolation failed: outbound request to google.com succeeded")


# ---------------------------------------------------------------------------
# Secret file lifecycle — non-proxy agents only
# ---------------------------------------------------------------------------

@skip_unless_integration
class TestSecretFileLifecycle:
    """Verify the secret file write/read/delete pattern inside a real sandbox.

    This is the mechanism non-proxy agents use to inject API keys: write the
    key to a file, cat it into an env var, delete the file, verify the env
    var is set and the file is gone.
    """

    @pytest.fixture(autouse=True)
    def _check_docker(self):
        if not _docker_available():
            pytest.skip("Docker is not available or not running")

    @pytest.fixture(params=[a for a in VALID_AGENTS
                            if not get_agent(a)["uses_proxy"]])
    def agent(self, request):
        return request.param

    @pytest.fixture
    def test_sandbox(self, agent):
        agent_config = get_agent(agent)
        sandbox = DockerSandbox(REPO_ROOT)
        tag = sandbox.ensure_image(agent)
        name = f"agent-loop-selftest-secret-{agent}"
        sandbox.remove_sandbox(name)

        git_common_dir = DockerSandbox._resolve_git_common_dir(os.getcwd())
        sandbox._docker_sandbox_create(
            name, tag, os.getcwd(), git_common_dir,
            sandbox_agent=agent_config["sandbox_agent"])
        yield name

        sandbox.remove_sandbox(name)

    def test_write_and_read_secret(self, test_sandbox):
        """Write a secret file and read it back."""
        secret = "test-api-key-abc123"
        write = subprocess.run(
            ["docker", "sandbox", "exec", "-i", test_sandbox,
             "tee", "/tmp/.agent-api-key"],
            input=secret, text=True, check=False,
            stdout=subprocess.DEVNULL, timeout=30,
        )
        assert write.returncode == 0, "Failed to write secret file"

        read = subprocess.run(
            ["docker", "sandbox", "exec", test_sandbox,
             "cat", "/tmp/.agent-api-key"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=30,
        )
        assert read.returncode == 0
        assert read.stdout.strip() == secret

    def test_secret_lifecycle_via_shell(self, agent, test_sandbox):
        """Exercise the full cat-into-env-var, delete, verify pattern."""
        agent_config = get_agent(agent)
        env_var = agent_config["env_var_name"]
        secret = "lifecycle-test-key-xyz789"

        # Write the secret file
        subprocess.run(
            ["docker", "sandbox", "exec", "-i", test_sandbox,
             "tee", "/tmp/.agent-api-key"],
            input=secret, text=True, check=True,
            stdout=subprocess.DEVNULL, timeout=30,
        )

        # Run the same shell pattern used by run_iteration:
        # export VAR="$(cat file)" && rm file && echo $VAR && test ! -f file
        inner_cmd = (
            f'export {env_var}="$(cat /tmp/.agent-api-key)" && '
            f'rm /tmp/.agent-api-key && '
            f'echo "${env_var}" && '
            f'test ! -f /tmp/.agent-api-key'
        )
        result = subprocess.run(
            ["docker", "sandbox", "exec", test_sandbox,
             "sh", "-c", inner_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=30,
        )
        assert result.returncode == 0, (
            f"Secret lifecycle failed: stderr={result.stderr}")
        assert result.stdout.strip() == secret, (
            f"Env var mismatch: expected {secret!r}, got {result.stdout.strip()!r}")


# ---------------------------------------------------------------------------
# Claude-specific auth tests — require proxy + token
# ---------------------------------------------------------------------------

@skip_unless_integration
class TestClaudeAuth:
    """Auth and proxy tests specific to the claude agent."""

    @pytest.fixture(autouse=True)
    def _check_prerequisites(self):
        if not _docker_available():
            pytest.skip("Docker is not available or not running")
        if not _token_available("claude"):
            pytest.skip("No valid claude token — run: ralph store-token")

    @pytest.fixture
    def proxy_port(self):
        port = proxy_port_for_agent("claude")
        ensure_proxy("claude", port, REPO_ROOT)
        yield port
        stop_proxy("claude")

    @pytest.fixture
    def test_sandbox(self):
        agent_config = get_agent("claude")
        sandbox = DockerSandbox(REPO_ROOT)
        tag = sandbox.ensure_image("claude")
        name = "agent-loop-selftest-claude"
        sandbox.remove_sandbox(name)

        git_common_dir = DockerSandbox._resolve_git_common_dir(os.getcwd())
        sandbox._docker_sandbox_create(
            name, tag, os.getcwd(), git_common_dir,
            sandbox_agent=agent_config["sandbox_agent"])
        DockerSandbox.apply_network_policy(name, agent_config["allowed_hosts"])
        yield name

        sandbox.remove_sandbox(name)

    def test_proxy_connectivity_from_sandbox(self, proxy_port, test_sandbox):
        """Verify the proxy health endpoint is reachable from inside the sandbox."""
        result = subprocess.run(
            ["docker", "sandbox", "exec", test_sandbox,
             "curl", "-sf", "--max-time", "5",
             f"http://host.docker.internal:{proxy_port}/health"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=120,
        )
        assert result.returncode == 0, (
            f"Proxy not reachable from sandbox: {result.stderr}")

    def test_claude_execution_via_proxy(self, proxy_port, test_sandbox):
        """Verify Claude can authenticate and respond through the proxy."""
        result = subprocess.run(
            ["docker", "sandbox", "exec",
             "-e", "CLAUDE_CODE_OAUTH_TOKEN=phantom",
             "-e", f"ANTHROPIC_BASE_URL=http://host.docker.internal:{proxy_port}/v1",
             test_sandbox,
             "claude", "-p", "say ok", "--model", "haiku"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=120,
        )
        assert result.returncode == 0, (
            f"Claude execution failed: exit={result.returncode} "
            f"stderr={result.stderr[:200]}")


# ---------------------------------------------------------------------------
# Selftest command — runs the full pipeline for claude
# ---------------------------------------------------------------------------

@skip_unless_integration
class TestSelftest:
    @pytest.fixture(autouse=True)
    def _check_prerequisites(self):
        if not _docker_available():
            pytest.skip("Docker is not available or not running")
        if not _token_available("claude"):
            pytest.skip("No valid claude token — run: ralph store-token")

    def test_selftest_command(self):
        """Verify the selftest command runs the full pipeline and passes."""
        rc = selftest("claude", REPO_ROOT)
        assert rc == 0, "selftest reported failures"
