"""Integration tests for ralph — exercises the full sandbox pipeline.

These tests require Docker Desktop running and a valid token stored
in Keychain. They are skipped by default.

Enable with:
    RALPH_INTEGRATION_TESTS=1 pytest -m integration tools/ralph/tests/test_integration.py -v
"""

import subprocess
import time

import pytest

from dotlib import DOTFILES_DIR
from ralph.proxy import proxy_port_for_agent, ensure_proxy, stop_proxy
from ralph.selftest import selftest
from ralph.sandbox.docker import DockerSandbox
from ralph.token import read_token_from_keychain

# Skip entire module unless integration tests are enabled
pytestmark = pytest.mark.integration

REPO_ROOT = DOTFILES_DIR

AGENT = "claude"
SELFTEST_SANDBOX = f"agent-loop-selftest-{AGENT}"


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


@skip_unless_integration
class TestRalphIntegration:
    """End-to-end integration tests for the ralph sandbox pipeline."""

    @pytest.fixture(autouse=True)
    def _check_prerequisites(self):
        """Skip tests if Docker or token is not available."""
        if not _docker_available():
            pytest.skip("Docker is not available or not running")
        if not _token_available(AGENT):
            pytest.skip("No valid token stored — run: ralph store-token")

    @pytest.fixture
    def proxy_port(self):
        """Start the proxy, yield the port, and stop on cleanup."""
        port = proxy_port_for_agent(AGENT)
        ensure_proxy(AGENT, port, REPO_ROOT)
        yield port
        stop_proxy(AGENT)

    @pytest.fixture
    def sandbox_image(self):
        """Ensure the sandbox image is built and return its tag."""
        sandbox = DockerSandbox(REPO_ROOT)
        tag = sandbox.ensure_image(AGENT)
        return tag

    @pytest.fixture
    def test_sandbox(self, sandbox_image):
        """Create a test sandbox and remove it after the test."""
        sandbox = DockerSandbox(REPO_ROOT)
        # Clean up any leftover sandbox from a previous failed run
        sandbox.remove_sandbox(SELFTEST_SANDBOX)

        git_common_dir = DockerSandbox._resolve_git_common_dir(os.getcwd())
        sandbox._docker_sandbox_create(
            SELFTEST_SANDBOX, sandbox_image, os.getcwd(), git_common_dir)
        DockerSandbox.apply_network_policy(SELFTEST_SANDBOX)
        yield SELFTEST_SANDBOX

        sandbox.remove_sandbox(SELFTEST_SANDBOX)

    def test_image_build(self, sandbox_image):
        """Verify that the sandbox image builds successfully."""
        assert sandbox_image.startswith(f"agent-loop-sandbox-{AGENT}:v")

        # Verify image exists locally
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

    def test_network_isolation(self, test_sandbox):
        """Verify outbound network requests are blocked by the network policy."""
        result = subprocess.run(
            ["docker", "sandbox", "exec", test_sandbox,
             "curl", "-s", "--max-time", "5", "https://google.com"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, timeout=120,
        )
        assert result.returncode != 0, (
            "Network isolation failed: outbound request to google.com succeeded")

    def test_selftest_command(self):
        """Verify the selftest command runs the full pipeline and passes."""
        rc = selftest(AGENT, REPO_ROOT)
        assert rc == 0, "selftest reported failures"
