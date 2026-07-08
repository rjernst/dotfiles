"""Unit tests for ralph.runtime.docker_sandbox — DockerSandboxRuntime backend."""

import datetime
import json
import os
import shlex
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from ralph.runtime import (
    Runtime, load_runtime_config, create_runtime, resolve_project_runtime,
)
from ralph.runtime.container import DockerContainerRuntime
from ralph.runtime.docker_sandbox import DockerSandboxRuntime
from ralph.runtime.nono import NonoRuntime
from ralph.runtime.tart import TartRuntime


# ---------------------------------------------------------------------------
# load_runtime_config
# ---------------------------------------------------------------------------

class TestLoadRuntimeConfig:
    def test_default_when_no_config(self, tmp_path):
        """No .agent-loop/config.json returns docker default."""
        assert load_runtime_config(str(tmp_path)) == {"type": "docker-sandbox"}

    def test_default_when_no_agent_loop_dir(self, tmp_path):
        """No .agent-loop directory at all returns docker default."""
        assert load_runtime_config(str(tmp_path)) == {"type": "docker-sandbox"}

    def test_docker_type(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "docker-sandbox"}')
        assert load_runtime_config(str(tmp_path)) == {"type": "docker-sandbox"}

    def test_tart_type_with_base_image(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {
            "type": "tart",
            "base_image": "ghcr.io/cirruslabs/macos-sequoia-xcode:latest",
        }
        (config_dir / "config.json").write_text(json.dumps(config))
        result = load_runtime_config(str(tmp_path))
        assert result["type"] == "tart"
        assert result["base_image"] == "ghcr.io/cirruslabs/macos-sequoia-xcode:latest"

    def test_tart_type_with_optional_fields(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {
            "type": "tart",
            "base_image": "ghcr.io/cirruslabs/macos-sequoia-xcode:latest",
            "cpu": 4,
            "memory_gb": 8,
        }
        (config_dir / "config.json").write_text(json.dumps(config))
        result = load_runtime_config(str(tmp_path))
        assert result["type"] == "tart"
        assert result["cpu"] == 4
        assert result["memory_gb"] == 8

    def test_docker_container_type(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "docker-container"}')
        assert load_runtime_config(str(tmp_path)) == {"type": "docker-container"}

    def test_missing_type_defaults_to_docker_sandbox(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"some_key": "value"}')
        result = load_runtime_config(str(tmp_path))
        assert result["type"] == "docker-sandbox"

    def test_unknown_type_raises_value_error(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "kubernetes"}')
        with pytest.raises(ValueError, match="unknown runtime type"):
            load_runtime_config(str(tmp_path))

    def test_unknown_type_message_lists_nono(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "kubernetes"}')
        with pytest.raises(ValueError) as exc_info:
            load_runtime_config(str(tmp_path))
        assert "'nono'" in str(exc_info.value)

    def test_nono_type(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "nono"}')
        assert load_runtime_config(str(tmp_path)) == {"type": "nono"}

    def test_nono_type_with_network_and_hosts(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {
            "type": "nono",
            "allowed_hosts": ["registry.npmjs.org"],
            "network": "unrestricted",
        }
        (config_dir / "config.json").write_text(json.dumps(config))
        result = load_runtime_config(str(tmp_path))
        assert result["type"] == "nono"
        assert result["network"] == "unrestricted"
        assert result["allowed_hosts"] == ["registry.npmjs.org"]

    def test_network_filtered_accepted(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            '{"type": "nono", "network": "filtered"}')
        assert load_runtime_config(str(tmp_path))["network"] == "filtered"

    def test_unknown_network_raises_value_error(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            '{"type": "nono", "network": "open"}')
        with pytest.raises(ValueError, match="unknown network mode 'open'"):
            load_runtime_config(str(tmp_path))

    def test_network_not_validated_for_other_runtimes(self, tmp_path):
        """'network' is nono-only; other backends ignore unknown keys."""
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            '{"type": "docker-sandbox", "network": "open"}')
        assert load_runtime_config(str(tmp_path))["network"] == "open"

    def test_omitted_network_is_not_defaulted_into_config(self, tmp_path):
        """The default lives in NonoRuntime, not in the loaded config."""
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "nono"}')
        assert "network" not in load_runtime_config(str(tmp_path))

    def test_allowed_hosts_passed_through(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {"type": "docker-sandbox", "allowed_hosts": ["pypi.org", "example.com"]}
        (config_dir / "config.json").write_text(json.dumps(config))
        result = load_runtime_config(str(tmp_path))
        assert result["allowed_hosts"] == ["pypi.org", "example.com"]

    def test_malformed_json_raises(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            load_runtime_config(str(tmp_path))


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------

class TestRuntime:
    """Runtime interface methods raise NotImplementedError."""

    def test_proxy_host_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().proxy_host()

    def test_ensure_image_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().ensure_image("claude")

    def test_ensure_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().ensure_sandbox("claude", "main", "/work")

    def test_setup_git_config_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().setup_git_config("name", "user", "email")

    def test_run_iteration_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().run_iteration("name", "spec", "model")

    def test_preflight_backend_checks_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime()._preflight_backend_checks("name")

    def test_cleanup_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().cleanup_sandbox("agent", "branch")

    def test_prune_sandboxes_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().prune_sandboxes("agent")

    def test_remove_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().remove_sandbox("name")

    def test_check_prerequisites_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().check_prerequisites()

    def test_check_in_sync_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().check_in_sync("name", "/work", None)

    def test_reset_to_host_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().reset_to_host("name", "/work", None)

    def test_sync_to_host_raises(self):
        with pytest.raises(NotImplementedError):
            Runtime().sync_to_host("name", "abc", "def", "/work")

    def test_sandbox_name_shared(self):
        """sandbox_name is shared logic, not abstract."""
        assert Runtime.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"

    def test_docker_sandbox_inherits_sandbox_name(self):
        """DockerSandboxRuntime inherits sandbox_name from Runtime."""
        assert DockerSandboxRuntime.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"


# ---------------------------------------------------------------------------
# proxy_listen_addr
# ---------------------------------------------------------------------------

class TestProxyListenAddr:
    def test_base_defaults_to_dual_stack(self):
        assert Runtime().proxy_listen_addr() == "::"

    def test_docker_sandbox_uses_loopback(self):
        assert create_runtime(
            "docker-sandbox", "/dotfiles").proxy_listen_addr() == "127.0.0.1"

    def test_docker_container_uses_loopback(self):
        assert create_runtime(
            "docker-container", "/dotfiles").proxy_listen_addr() == "127.0.0.1"

    def test_tart_uses_dual_stack(self):
        runtime = create_runtime("tart", "/dotfiles",
                                 base_image="ghcr.io/example/vm:latest")
        assert runtime.proxy_listen_addr() == "::"

    def test_nono_uses_loopback(self):
        assert create_runtime(
            "nono", "/dotfiles").proxy_listen_addr() == "127.0.0.1"

    @pytest.mark.parametrize("runtime_type", ["docker-sandbox",
                                              "docker-container"])
    def test_env_overrides_backend_default(self, monkeypatch, runtime_type):
        monkeypatch.setenv("RALPH_PROXY_LISTEN_ADDR", "::1")
        runtime = create_runtime(runtime_type, "/dotfiles")
        assert runtime.proxy_listen_addr() == "::1"

    def test_env_overrides_base_default(self, monkeypatch):
        monkeypatch.setenv("RALPH_PROXY_LISTEN_ADDR", "192.168.1.5")
        assert Runtime().proxy_listen_addr() == "192.168.1.5"

    def test_empty_env_falls_back_to_backend_default(self, monkeypatch):
        monkeypatch.setenv("RALPH_PROXY_LISTEN_ADDR", "")
        runtime = create_runtime("docker-sandbox", "/dotfiles")
        assert runtime.proxy_listen_addr() == "127.0.0.1"


# ---------------------------------------------------------------------------
# uses_credential_proxy
# ---------------------------------------------------------------------------

class TestUsesCredentialProxy:
    def test_base_uses_credential_proxy(self):
        assert Runtime().uses_credential_proxy is True

    @pytest.mark.parametrize("runtime_type", ["docker-sandbox",
                                              "docker-container"])
    def test_docker_backends_use_credential_proxy(self, runtime_type):
        assert create_runtime(
            runtime_type, "/dotfiles").uses_credential_proxy is True

    def test_tart_uses_credential_proxy(self):
        runtime = create_runtime("tart", "/dotfiles", base_image="vm:latest")
        assert runtime.uses_credential_proxy is True

    def test_nono_does_not_use_credential_proxy(self):
        assert create_runtime(
            "nono", "/dotfiles").uses_credential_proxy is False


# ---------------------------------------------------------------------------
# resolve_project_runtime
# ---------------------------------------------------------------------------

class TestResolveProjectRuntime:
    @staticmethod
    def _git(repo_root):
        git = MagicMock()
        git.output.return_value = repo_root
        return git

    @classmethod
    def _addr(cls, repo_root, dotfiles_dir="/dotfiles"):
        """The listen address cli.py derives from the resolved runtime."""
        return resolve_project_runtime(
            cls._git(repo_root), dotfiles_dir)[1].proxy_listen_addr()

    def test_returns_type_and_runtime(self, tmp_path):
        cfg_dir = tmp_path / ".agent-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text('{"type": "nono"}')
        runtime_type, runtime = resolve_project_runtime(
            self._git(str(tmp_path)), "/dotfiles")
        assert runtime_type == "nono"
        assert isinstance(runtime, NonoRuntime)

    def test_defaults_to_docker_sandbox(self, tmp_path):
        runtime_type, runtime = resolve_project_runtime(
            self._git(str(tmp_path)), "/dotfiles")
        assert runtime_type == "docker-sandbox"
        assert isinstance(runtime, DockerSandboxRuntime)

    def test_bad_config_falls_back_to_base_runtime(self, tmp_path):
        cfg_dir = tmp_path / ".agent-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("{not json")
        runtime_type, runtime = resolve_project_runtime(
            self._git(str(tmp_path)), "/dotfiles")
        assert runtime_type is None
        assert type(runtime) is Runtime
        assert runtime.uses_credential_proxy is True

    def test_listen_addr_uses_project_runtime_default(self, tmp_path):
        assert self._addr(str(tmp_path)) == "127.0.0.1"

    def test_listen_addr_reads_configured_runtime_type(self, tmp_path):
        cfg_dir = tmp_path / ".agent-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text(
            json.dumps({"type": "tart", "base_image": "vm:latest"}))
        assert self._addr(str(tmp_path)) == "::"

    def test_listen_addr_bad_config_falls_back_to_base_default(self, tmp_path):
        cfg_dir = tmp_path / ".agent-loop"
        cfg_dir.mkdir()
        (cfg_dir / "config.json").write_text("{not json")
        assert self._addr(str(tmp_path)) == "::"

    def test_listen_addr_env_override_applies(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RALPH_PROXY_LISTEN_ADDR", "::1")
        assert self._addr(str(tmp_path)) == "::1"

    def test_listen_addr_env_override_applies_on_fallback(self, monkeypatch):
        monkeypatch.setenv("RALPH_PROXY_LISTEN_ADDR", "::1")
        git = MagicMock()
        git.output.side_effect = RuntimeError("not a git repo")
        runtime_type, runtime = resolve_project_runtime(git, "/dotfiles")
        assert runtime_type is None
        assert runtime.proxy_listen_addr() == "::1"


# ---------------------------------------------------------------------------
# create_runtime factory
# ---------------------------------------------------------------------------

class TestCreateRuntime:
    def test_docker_sandbox_returns_docker_sandbox_runtime(self):
        backend = create_runtime("docker-sandbox", "/dotfiles")
        assert isinstance(backend, DockerSandboxRuntime)
        assert backend.dotfiles_dir == "/dotfiles"

    def test_tart_returns_tart_runtime(self):
        from ralph.runtime.tart import TartRuntime
        backend = create_runtime(
            "tart", "/dotfiles",
            base_image="ghcr.io/cirruslabs/macos-sequoia-xcode:latest")
        assert isinstance(backend, TartRuntime)
        assert backend.dotfiles_dir == "/dotfiles"
        assert backend.base_image == "ghcr.io/cirruslabs/macos-sequoia-xcode:latest"

    def test_tart_reads_dependencies_from_project_dir(self, tmp_path):
        agent_loop = tmp_path / ".agent-loop"
        agent_loop.mkdir()
        (agent_loop / "dependencies").write_text("brew install jq\n")
        backend = create_runtime(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path))
        assert backend.dependencies_content == "brew install jq\n"

    def test_tart_no_dependencies_file(self, tmp_path):
        backend = create_runtime(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path))
        assert backend.dependencies_content == ""

    def test_tart_explicit_dependencies_not_overridden(self, tmp_path):
        """If dependencies_content is passed explicitly, don't read from file."""
        agent_loop = tmp_path / ".agent-loop"
        agent_loop.mkdir()
        (agent_loop / "dependencies").write_text("from file\n")
        backend = create_runtime(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path),
            dependencies_content="explicit\n")
        assert backend.dependencies_content == "explicit\n"

    def test_unknown_type_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown runtime type 'podman'"):
            create_runtime("podman", "/dotfiles")

    def test_nono_returns_nono_runtime(self):
        backend = create_runtime("nono", "/dotfiles")
        assert isinstance(backend, NonoRuntime)
        assert backend.dotfiles_dir == "/dotfiles"
        assert backend.allowed_hosts == ()
        assert backend.network == "filtered"
        assert backend.project_dir is None

    def test_nono_config_passed_through(self, tmp_path):
        backend = create_runtime(
            "nono", "/dotfiles",
            allowed_hosts=["registry.npmjs.org", "pypi.org"],
            network="unrestricted", project_dir=str(tmp_path))
        assert backend.allowed_hosts == ("registry.npmjs.org", "pypi.org")
        assert backend.network == "unrestricted"
        assert backend.project_dir == str(tmp_path)

    def test_nono_rejects_unknown_network(self):
        with pytest.raises(ValueError, match="unknown network mode 'open'"):
            create_runtime("nono", "/dotfiles", network="open")

    def test_nono_receives_credentials(self):
        """auth_mode/token_data reach NonoRuntime for the credential route."""
        token_data = {"baseUrl": "https://gw.example.com"}
        backend = create_runtime("nono", "/dotfiles", auth_mode="gateway",
                                 token_data=token_data)
        assert backend.auth_mode == "gateway"
        assert backend.token_data == token_data

    def test_nono_credentials_default_to_none(self):
        backend = create_runtime("nono", "/dotfiles")
        assert backend.auth_mode is None
        assert backend.token_data is None

    def test_credentials_not_passed_to_other_backends(self):
        """Backends without their own injection ignore credential kwargs."""
        backend = create_runtime("docker-sandbox", "/dotfiles",
                                 auth_mode="oauth", token_data={"a": 1})
        assert isinstance(backend, DockerSandboxRuntime)

    def test_tart_config_excludes_credentials(self, tmp_path):
        """Credential kwargs never land in the tart config dict."""
        with patch("ralph.runtime.tart.TartRuntime") as mock_tart:
            create_runtime("tart", "/dotfiles", base_image="img:latest",
                           project_dir=str(tmp_path), auth_mode="oauth",
                           token_data={"accessToken": "secret"})
        config = mock_tart.call_args[1]["config"]
        assert config == {"base_image": "img:latest"}

    def test_kwargs_passed_through(self):
        """Extra kwargs don't break DockerSandboxRuntime creation."""
        backend = create_runtime(
            "docker-sandbox", "/dotfiles", base_image="foo", cpu=4)
        assert isinstance(backend, DockerSandboxRuntime)

    def test_docker_allowed_hosts_passed_through(self):
        """allowed_hosts from config reaches DockerSandboxRuntime."""
        backend = create_runtime(
            "docker-sandbox", "/dotfiles",
            allowed_hosts=["pypi.org", "example.com"])
        assert backend.allowed_hosts == ("pypi.org", "example.com")

    def test_docker_no_allowed_hosts_defaults_to_empty(self):
        backend = create_runtime("docker-sandbox", "/dotfiles")
        assert backend.allowed_hosts == ()

    def test_docker_container_returns_docker_container_runtime(self):
        from ralph.runtime.container import DockerContainerRuntime
        backend = create_runtime("docker-container", "/dotfiles")
        assert isinstance(backend, DockerContainerRuntime)
        assert backend.dotfiles_dir == "/dotfiles"

    def test_docker_container_allowed_hosts_passed_through(self):
        from ralph.runtime.container import DockerContainerRuntime
        backend = create_runtime(
            "docker-container", "/dotfiles",
            allowed_hosts=["pypi.org", "example.com"])
        assert isinstance(backend, DockerContainerRuntime)
        assert backend.allowed_hosts == ("pypi.org", "example.com")

    def test_docker_container_no_allowed_hosts_defaults_to_empty(self):
        from ralph.runtime.container import DockerContainerRuntime
        backend = create_runtime("docker-container", "/dotfiles")
        assert backend.allowed_hosts == ()


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.parse_base_image
# ---------------------------------------------------------------------------

class TestSandboxParseBaseImage:
    def test_extracts_from_line(self):
        content = "FROM docker/sandbox-templates:claude-code\nUSER root"
        assert DockerSandboxRuntime.parse_base_image(content) == "docker/sandbox-templates:claude-code"

    def test_returns_none_when_no_from(self):
        assert DockerSandboxRuntime.parse_base_image("RUN echo hi") is None

    def test_ignores_comment_lines(self):
        content = "# FROM fake:image\nFROM real:latest"
        assert DockerSandboxRuntime.parse_base_image(content) == "real:latest"

    def test_returns_final_stage_in_multistage(self):
        content = "FROM builder:latest AS build\nRUN make\nFROM runtime:slim"
        assert DockerSandboxRuntime.parse_base_image(content) == "runtime:slim"


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.content_hash
# ---------------------------------------------------------------------------

class TestSandboxContentHash:
    def test_deterministic(self):
        h1 = DockerSandboxRuntime.content_hash("FROM a", "digest1")
        h2 = DockerSandboxRuntime.content_hash("FROM a", "digest1")
        assert h1 == h2

    def test_changes_when_dockerfile_changes(self):
        h1 = DockerSandboxRuntime.content_hash("FROM a\nRUN echo old", "digest1")
        h2 = DockerSandboxRuntime.content_hash("FROM a\nRUN echo new", "digest1")
        assert h1 != h2

    def test_changes_when_base_digest_changes(self):
        df = "FROM a\nRUN echo same"
        h1 = DockerSandboxRuntime.content_hash(df, "sha256:aaa")
        h2 = DockerSandboxRuntime.content_hash(df, "sha256:bbb")
        assert h1 != h2

    def test_length_is_8(self):
        h = DockerSandboxRuntime.content_hash("FROM a", "d")
        assert len(h) == 8


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.image_tag
# ---------------------------------------------------------------------------

class TestSandboxImageTag:
    def test_format(self):
        tag = DockerSandboxRuntime.image_tag("claude", "abc123de")
        assert tag == "agent-loop-sandbox-claude:vabc123de"

    def test_custom_agent(self):
        tag = DockerSandboxRuntime.image_tag("codex", "xyz")
        assert tag == "agent-loop-sandbox-codex:vxyz"


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.parse_dependencies
# ---------------------------------------------------------------------------

class TestSandboxParseDependencies:
    def test_basic_package_list(self):
        content = "openjdk-21-jdk\npython3-venv\nnodejs"
        assert DockerSandboxRuntime.parse_dependencies(content) == [
            "openjdk-21-jdk", "python3-venv", "nodejs"
        ]

    def test_comment_only_lines_skipped(self):
        content = "# This is a comment\npkg1\n# Another comment\npkg2"
        assert DockerSandboxRuntime.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_inline_comments_stripped(self):
        content = "pkg1 # this is a comment\npkg2 # another"
        assert DockerSandboxRuntime.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_blank_lines_skipped(self):
        content = "pkg1\n\n\npkg2\n\npkg3"
        assert DockerSandboxRuntime.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_whitespace_handling(self):
        content = "  pkg1  \n\tpkg2\t\n  pkg3  # comment  "
        assert DockerSandboxRuntime.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_empty_content(self):
        assert DockerSandboxRuntime.parse_dependencies("") == []

    def test_only_comments_and_blanks(self):
        content = "# comment\n\n# another\n  \n"
        assert DockerSandboxRuntime.parse_dependencies(content) == []

    def test_rejects_shell_injection(self):
        with pytest.raises(ValueError, match="invalid package name"):
            DockerSandboxRuntime.parse_dependencies("pkg; rm -rf /")

    def test_rejects_uppercase_names(self):
        with pytest.raises(ValueError, match="invalid package name"):
            DockerSandboxRuntime.parse_dependencies("BadPkg")

    def test_accepts_arch_qualifier(self):
        result = DockerSandboxRuntime.parse_dependencies("libc6:amd64")
        assert result == ["libc6:amd64"]

    def test_accepts_version_pinning(self):
        result = DockerSandboxRuntime.parse_dependencies("openjdk-21-jdk=21.0.1+12-1")
        assert result == ["openjdk-21-jdk=21.0.1+12-1"]


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.generate_project_dockerfile
# ---------------------------------------------------------------------------

class TestSandboxGenerateProjectDockerfile:
    def test_single_package(self):
        result = DockerSandboxRuntime.generate_project_dockerfile(["openjdk-21-jdk"])
        assert "apt-get install -y --no-install-recommends" in result
        assert "openjdk-21-jdk" in result

    def test_multiple_packages_joined(self):
        result = DockerSandboxRuntime.generate_project_dockerfile(["pkg1", "pkg2", "pkg3"])
        assert "'pkg1' 'pkg2' 'pkg3'" in result

    def test_packages_are_shell_quoted(self):
        result = DockerSandboxRuntime.generate_project_dockerfile(["pkg; rm -rf /"])
        assert "\"pkg; rm -rf /\"" not in result
        assert "'pkg; rm -rf /'" in result

    def test_contains_arg_and_from(self):
        result = DockerSandboxRuntime.generate_project_dockerfile(["pkg1"])
        assert "ARG BASE_IMAGE" in result
        assert "FROM ${BASE_IMAGE}" in result

    def test_user_switching(self):
        result = DockerSandboxRuntime.generate_project_dockerfile(["pkg1"])
        lines = result.splitlines()
        assert "USER root" in lines
        assert "USER agent" in lines
        assert lines.index("USER root") < lines.index("USER agent")

    def test_apt_cleanup(self):
        result = DockerSandboxRuntime.generate_project_dockerfile(["pkg1"])
        assert "rm -rf /var/lib/apt/lists/*" in result

    def test_no_install_recommends(self):
        result = DockerSandboxRuntime.generate_project_dockerfile(["pkg1"])
        assert "--no-install-recommends" in result


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.find_project_config
# ---------------------------------------------------------------------------

class TestSandboxFindProjectConfig:
    def test_returns_none_when_no_agent_loop_dir(self, tmp_path):
        result = DockerSandboxRuntime.find_project_config(str(tmp_path))
        assert result is None

    def test_returns_none_when_agent_loop_empty(self, tmp_path):
        (tmp_path / ".agent-loop").mkdir()
        result = DockerSandboxRuntime.find_project_config(str(tmp_path))
        assert result is None

    def test_prefers_dockerfile_over_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = DockerSandboxRuntime.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")

    def test_returns_dependencies_when_no_dockerfile(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        config_type, path = DockerSandboxRuntime.find_project_config(str(tmp_path))
        assert config_type == "dependencies"
        assert path == str(al / "dependencies")

    def test_returns_dockerfile_when_no_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = DockerSandboxRuntime.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.project_image_tag
# ---------------------------------------------------------------------------

class TestSandboxProjectImageTag:
    def test_includes_agent_and_project_in_tag(self):
        tag = DockerSandboxRuntime.project_image_tag("claude", "myproject", "base:v1", "content")
        assert tag.startswith("agent-loop-sandbox-claude-myproject:v")

    def test_hash_is_8_chars(self):
        tag = DockerSandboxRuntime.project_image_tag("claude", "proj", "base:v1", "content")
        chash = tag.split(":v")[1]
        assert len(chash) == 8

    def test_different_content_produces_different_hash(self):
        tag1 = DockerSandboxRuntime.project_image_tag("claude", "proj", "base:v1", "content-a")
        tag2 = DockerSandboxRuntime.project_image_tag("claude", "proj", "base:v1", "content-b")
        assert tag1 != tag2

    def test_same_content_produces_same_hash(self):
        tag1 = DockerSandboxRuntime.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = DockerSandboxRuntime.project_image_tag("claude", "proj", "base:v1", "content")
        assert tag1 == tag2

    def test_different_base_tag_produces_different_hash(self):
        tag1 = DockerSandboxRuntime.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = DockerSandboxRuntime.project_image_tag("claude", "proj", "base:v2", "content")
        assert tag1 != tag2


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.ensure_project_image
# ---------------------------------------------------------------------------

class TestSandboxEnsureProjectImage:
    @staticmethod
    def _make_sandbox(tmp_path):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text("FROM base:latest\nRUN echo hi")
        return DockerSandboxRuntime(str(tmp_path))

    def test_returns_base_tag_when_no_project_config(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        result = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert result == "base:v1"

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_builds_when_tag_missing_with_dependencies(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\npkg2\n")
        # image_exists returns False (tag not cached)
        mock_run.return_value = MagicMock(returncode=1)
        tag = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert tag.startswith("agent-loop-sandbox-claude-")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1
        # Verify --build-arg BASE_IMAGE passed
        build_cmd = build_calls[0][0][0]
        assert "--build-arg" in build_cmd
        assert "BASE_IMAGE=base:v1" in build_cmd

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_builds_with_dockerfile_sandbox(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "Dockerfile.sandbox").write_text(
            "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\nRUN echo custom\n")
        mock_run.return_value = MagicMock(returncode=1)
        tag = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert tag.startswith("agent-loop-sandbox-claude-")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1
        build_cmd = build_calls[0][0][0]
        # Uses -f Dockerfile.sandbox with .agent-loop/ as context
        assert "-f" in build_cmd
        assert "Dockerfile.sandbox" in build_cmd
        assert str(al) in build_cmd

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_skips_build_when_tag_exists(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        # image_exists returns True (tag cached)
        mock_run.return_value = MagicMock(returncode=0)
        tag = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert tag.startswith("agent-loop-sandbox-claude-")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 0

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_force_rebuild_forces_build(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        mock_run.return_value = MagicMock(returncode=0)
        sb.ensure_project_image("claude", "base:v1", str(tmp_path),
                                force_rebuild=True)
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_project_name_derived_from_dir(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        project = tmp_path / "elasticsearch"
        project.mkdir()
        al = project / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        mock_run.return_value = MagicMock(returncode=1)
        tag = sb.ensure_project_image("claude", "base:v1", str(project))
        assert "elasticsearch" in tag

    def test_rejects_trailing_slash(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        with pytest.raises(ValueError, match="must not end with /"):
            sb.ensure_project_image("claude", "base:v1", "/some/path/")


# ---------------------------------------------------------------------------
# DockerSandboxRuntime._parse_docker_timestamp
# ---------------------------------------------------------------------------

class TestSandboxParseDockerTimestamp:
    def test_z_suffix(self):
        dt = DockerSandboxRuntime._parse_docker_timestamp("2024-06-15T10:30:00Z")
        assert dt.year == 2024 and dt.month == 6

    def test_truncates_nanoseconds(self):
        dt = DockerSandboxRuntime._parse_docker_timestamp("2024-06-15T10:30:00.123456789Z")
        assert dt.microsecond == 123456

    def test_offset_format(self):
        dt = DockerSandboxRuntime._parse_docker_timestamp("2024-06-15T10:30:00+00:00")
        assert dt.year == 2024


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.needs_rebuild
# ---------------------------------------------------------------------------

class TestSandboxNeedsRebuild:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return DockerSandboxRuntime(str(tmp_path))

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_true_when_image_missing(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=1)
        assert sb.needs_rebuild("claude") is True

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_true_when_image_old(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="2020-01-01T00:00:00Z\n")
        assert sb.needs_rebuild("claude") is True

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_false_when_image_recent(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="2099-01-01T00:00:00Z\n")
        assert sb.needs_rebuild("claude") is False


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.ensure_image
# ---------------------------------------------------------------------------

class TestSandboxEnsureImage:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return DockerSandboxRuntime(str(tmp_path))

    @staticmethod
    def _side_effect(image_exists=True, base_age="2099-01-01T00:00:00Z"):
        def fn(cmd, **kwargs):
            # docker image inspect <img> --format ... → base digest
            if cmd[1:3] == ["image", "inspect"] and "--format" in cmd:
                return MagicMock(returncode=0, stdout="sha256:abc\n")
            # docker image inspect <tag> → existence check
            if cmd[1:3] == ["image", "inspect"]:
                rc = 0 if image_exists else 1
                return MagicMock(returncode=rc)
            # docker inspect --format {{.Created}} <img> → age check
            if cmd[1] == "inspect":
                return MagicMock(returncode=0, stdout=f"{base_age}\n")
            return MagicMock(returncode=0)
        return fn

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_skips_build_when_tag_exists(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=True)
        tag = sb.ensure_image("claude")
        assert tag.startswith("agent-loop-sandbox-claude:v")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 0

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_builds_when_tag_missing(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=False)
        sb.ensure_image("claude")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_rebuild_forces_pull_and_build(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=True)
        sb.ensure_image("claude", force_rebuild=True)
        pull_calls = [c for c in mock_run.call_args_list
                      if c[0][0][1] == "pull"]
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(pull_calls) == 1
        assert len(build_calls) == 1

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_pulls_when_base_image_stale(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(
            image_exists=False, base_age="2020-01-01T00:00:00Z")
        sb.ensure_image("claude")
        pull_calls = [c for c in mock_run.call_args_list
                      if c[0][0][1] == "pull"]
        assert len(pull_calls) == 1


# ---------------------------------------------------------------------------
# Runtime.sandbox_name
# ---------------------------------------------------------------------------

class TestSandboxName:
    def test_simple_branch(self):
        assert DockerSandboxRuntime.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"

    def test_branch_with_slashes(self):
        assert DockerSandboxRuntime.sandbox_name("claude", "feature/foo") == "agent-loop-claude-feature-foo"

    def test_branch_with_multiple_slashes(self):
        assert DockerSandboxRuntime.sandbox_name("claude", "user/feature/bar") == "agent-loop-claude-user-feature-bar"

    def test_branch_uppercase_lowered(self):
        assert DockerSandboxRuntime.sandbox_name("claude", "Fix-Auth") == "agent-loop-claude-fix-auth"

    def test_consecutive_slashes_collapsed(self):
        assert DockerSandboxRuntime.sandbox_name("claude", "a//b") == "agent-loop-claude-a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert DockerSandboxRuntime.sandbox_name("claude", "-branch-") == "agent-loop-claude-branch"

    def test_custom_agent(self):
        assert DockerSandboxRuntime.sandbox_name("codex", "my-branch") == "agent-loop-codex-my-branch"


# ---------------------------------------------------------------------------
# Runtime.sandbox_name length (no limit for sbx backend)
# ---------------------------------------------------------------------------

class TestSandboxNameTruncation:
    def test_runtime_base_has_no_limit(self):
        """Base Runtime declares no length constraint — names are unclamped."""
        assert Runtime._max_sandbox_name_length() is None
        name = Runtime.sandbox_name("claude", "x" * 200)
        assert name == "agent-loop-claude-" + "x" * 200

    def test_docker_sandbox_has_no_limit(self):
        """DockerSandboxRuntime uses sbx (single daemon socket) — no name length constraint."""
        assert DockerSandboxRuntime._max_sandbox_name_length() is None

    def test_no_truncation_for_long_names(self):
        """Long names pass through unchanged since sbx has no socket path limit."""
        name = DockerSandboxRuntime.sandbox_name("claude", "x" * 200)
        assert name == "agent-loop-claude-" + "x" * 200

    @patch.object(DockerSandboxRuntime, "_max_sandbox_name_length")
    def test_truncation_applied_when_over_limit(self, mock_max):
        mock_max.return_value = 48
        name = DockerSandboxRuntime.sandbox_name(
            "claude", "extension-layers-modularization")
        assert len(name) <= 48
        assert name.startswith("agent-loop-claude-")

    @patch.object(DockerSandboxRuntime, "_max_sandbox_name_length")
    def test_truncation_is_deterministic(self, mock_max):
        """Same input always yields same truncated name (sandbox reuse)."""
        mock_max.return_value = 48
        a = DockerSandboxRuntime.sandbox_name("claude", "extension-layers-modularization")
        b = DockerSandboxRuntime.sandbox_name("claude", "extension-layers-modularization")
        assert a == b

    @patch.object(DockerSandboxRuntime, "_max_sandbox_name_length")
    def test_truncation_distinguishes_sibling_branches(self, mock_max):
        """Different branches sharing a common prefix get distinct names."""
        mock_max.return_value = 48
        long_prefix = "feature-with-a-really-long-descriptive-name"
        a = DockerSandboxRuntime.sandbox_name("claude", long_prefix + "-alpha")
        b = DockerSandboxRuntime.sandbox_name("claude", long_prefix + "-beta")
        assert a != b
        assert len(a) <= 48 and len(b) <= 48

    @patch.object(DockerSandboxRuntime, "_max_sandbox_name_length")
    def test_truncation_strips_trailing_hyphen_before_hash(self, mock_max):
        """Avoid '--' artifact when truncation lands on a hyphen."""
        mock_max.return_value = 48
        branch = "a" * 20 + "-tail-that-pushes-well-over-the-limit"
        name = DockerSandboxRuntime.sandbox_name("claude", branch)
        assert "--" not in name
        assert len(name) <= 48
        assert name[-9] == "-"
        assert all(c in "0123456789abcdef" for c in name[-8:])

    @patch.object(DockerSandboxRuntime, "_max_sandbox_name_length")
    def test_truncation_preserves_agent_loop_prefix(self, mock_max):
        """The 'agent-loop-<agent>-' prefix is always preserved."""
        mock_max.return_value = 48
        name = DockerSandboxRuntime.sandbox_name("claude", "x" * 100)
        assert name.startswith("agent-loop-claude-")


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.ensure_sandbox (mocked sbx)
# ---------------------------------------------------------------------------

class TestSandboxEnsureSandbox:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return DockerSandboxRuntime(str(tmp_path))

    @patch.object(DockerSandboxRuntime, "_write_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "_ensure_template_loaded")
    @patch.object(DockerSandboxRuntime, "_ensure_global_policy")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=False)
    def test_creates_new_sandbox(self, mock_exists, mock_img, mock_resolve,
                                 mock_policy_init, mock_tmpl, mock_create,
                                 mock_policy, mock_write_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"
        mock_policy_init.assert_called_once()
        mock_tmpl.assert_called_once_with("agent-loop-sandbox-claude:vabc")
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth", "agent-loop-sandbox-claude:vabc",
            "/work/fix-auth", "/repo/.git", sandbox_agent="claude")
        mock_policy.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            ["api.anthropic.com", "statsig.anthropic.com", "sentry.io"])
        mock_write_fp.assert_called_once()

    @patch.object(DockerSandboxRuntime, "_config_fingerprint", return_value="match")
    @patch.object(DockerSandboxRuntime, "_read_sandbox_fingerprint", return_value="match")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=True)
    def test_reuses_existing_sandbox(self, mock_exists, mock_img, mock_resolve,
                                     mock_read_fp, mock_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"

    @patch.object(DockerSandboxRuntime, "_config_fingerprint", return_value="match")
    @patch.object(DockerSandboxRuntime, "_read_sandbox_fingerprint", return_value="match")
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=True)
    def test_reuse_skips_create_and_policy(self, mock_exists, mock_img, mock_resolve,
                                           mock_create, mock_policy,
                                           mock_read_fp, mock_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        mock_create.assert_not_called()
        mock_policy.assert_not_called()

    @patch.object(DockerSandboxRuntime, "_write_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "_config_fingerprint", return_value="new")
    @patch.object(DockerSandboxRuntime, "_read_sandbox_fingerprint", return_value="old")
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "_ensure_template_loaded")
    @patch.object(DockerSandboxRuntime, "_ensure_global_policy")
    @patch.object(DockerSandboxRuntime, "remove_sandbox")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=True)
    def test_recreates_on_config_change(self, mock_exists, mock_img, mock_resolve,
                                        mock_remove, mock_policy_init, mock_tmpl,
                                        mock_create, mock_policy,
                                        mock_read_fp, mock_fp, mock_write_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"
        mock_remove.assert_called_once_with("agent-loop-claude-fix-auth")
        mock_create.assert_called_once()
        mock_policy.assert_called_once()
        mock_write_fp.assert_called_once()

    @patch.object(DockerSandboxRuntime, "_write_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "_ensure_template_loaded")
    @patch.object(DockerSandboxRuntime, "_ensure_global_policy")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(DockerSandboxRuntime, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=False)
    def test_calls_ensure_project_image_when_project_dir(
            self, mock_exists, mock_img, mock_proj, mock_resolve,
            mock_policy_init, mock_tmpl, mock_create, mock_policy, mock_write_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root")
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=False)
        mock_tmpl.assert_called_once_with(
            "agent-loop-sandbox-claude-myproj:vdef12345")
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude-myproj:vdef12345",
            "/work/fix-auth", "/repo/.git", sandbox_agent="claude")

    @patch.object(DockerSandboxRuntime, "_write_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "_ensure_template_loaded")
    @patch.object(DockerSandboxRuntime, "_ensure_global_policy")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=False)
    def test_skips_project_image_when_no_project_dir(
            self, mock_exists, mock_img, mock_resolve, mock_policy_init,
            mock_tmpl, mock_create, mock_policy, mock_write_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        with patch.object(DockerSandboxRuntime, "ensure_project_image") as mock_proj:
            sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
            mock_proj.assert_not_called()
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude:vabc",
            "/work/fix-auth", "/repo/.git", sandbox_agent="claude")

    @patch.object(DockerSandboxRuntime, "_write_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "_ensure_template_loaded")
    @patch.object(DockerSandboxRuntime, "_ensure_global_policy")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(DockerSandboxRuntime, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=False)
    def test_force_rebuild_passed_through(
            self, mock_exists, mock_img, mock_proj, mock_resolve,
            mock_policy_init, mock_tmpl, mock_create, mock_policy, mock_write_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root", force_rebuild=True)
        mock_img.assert_called_once_with("claude", force_rebuild=True)
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=True)

    @patch.object(DockerSandboxRuntime, "_write_sandbox_fingerprint")
    @patch.object(DockerSandboxRuntime, "apply_network_policy")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_create")
    @patch.object(DockerSandboxRuntime, "_ensure_template_loaded")
    @patch.object(DockerSandboxRuntime, "_ensure_global_policy")
    @patch.object(DockerSandboxRuntime, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandboxRuntime, "ensure_image", return_value="agent-loop-sandbox-cursor:vabc")
    @patch.object(DockerSandboxRuntime, "sandbox_exists", return_value=False)
    def test_cursor_uses_shell_sandbox_agent(self, mock_exists, mock_img,
                                             mock_resolve, mock_policy_init,
                                             mock_tmpl, mock_create,
                                             mock_policy, mock_write_fp):
        sb = DockerSandboxRuntime("/dotfiles")
        name = sb.ensure_sandbox("cursor", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-cursor-fix-auth"
        mock_create.assert_called_once_with(
            "agent-loop-cursor-fix-auth", "agent-loop-sandbox-cursor:vabc",
            "/work/fix-auth", "/repo/.git", sandbox_agent="shell")
        mock_policy.assert_called_once_with(
            "agent-loop-cursor-fix-auth",
            ["*.cursor.sh", "sentry.io"])


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.apply_network_policy
# ---------------------------------------------------------------------------

class TestSandboxApplyNetworkPolicy:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_claude_hosts(self, mock_run):
        allowed = ["api.anthropic.com", "statsig.anthropic.com", "sentry.io"]
        DockerSandboxRuntime("/dotfiles").apply_network_policy("agent-loop-claude-fix-auth", allowed)
        mock_run.assert_called_once_with(
            ["sbx", "policy", "allow", "network",
             "--sandbox", "agent-loop-claude-fix-auth",
             "host.docker.internal,localhost,api.anthropic.com,statsig.anthropic.com,sentry.io"],
            check=True,
        )

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_cursor_hosts(self, mock_run):
        allowed = ["*.cursor.sh", "sentry.io"]
        DockerSandboxRuntime("/dotfiles").apply_network_policy("agent-loop-cursor-fix-auth", allowed)
        mock_run.assert_called_once_with(
            ["sbx", "policy", "allow", "network",
             "--sandbox", "agent-loop-cursor-fix-auth",
             "host.docker.internal,localhost,*.cursor.sh,sentry.io"],
            check=True,
        )

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_extra_allowed_hosts(self, mock_run):
        sb = DockerSandboxRuntime("/dotfiles", allowed_hosts=["pypi.org", "registry.npmjs.org"])
        sb.apply_network_policy("agent-loop-claude-fix-auth",
                                ["api.anthropic.com", "statsig.anthropic.com", "sentry.io"])
        mock_run.assert_called_once_with(
            ["sbx", "policy", "allow", "network",
             "--sandbox", "agent-loop-claude-fix-auth",
             "host.docker.internal,localhost,api.anthropic.com,statsig.anthropic.com,sentry.io,pypi.org,registry.npmjs.org"],
            check=True,
        )

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_host_docker_internal_always_included(self, mock_run):
        DockerSandboxRuntime("/dotfiles").apply_network_policy("sandbox", ["example.com"])
        cmd = mock_run.call_args[0][0]
        hosts_arg = cmd[-1]
        assert "host.docker.internal" in hosts_arg
        assert "localhost" in hosts_arg
        assert hosts_arg.startswith("host.docker.internal,localhost,")


# ---------------------------------------------------------------------------
# DockerSandboxRuntime._ensure_global_policy
# ---------------------------------------------------------------------------

class TestEnsureGlobalPolicy:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_runs_sbx_policy_init_deny_all(self, mock_run):
        DockerSandboxRuntime._ensure_global_policy()
        mock_run.assert_called_once_with(
            ["sbx", "policy", "init", "deny-all"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_ignores_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        DockerSandboxRuntime._ensure_global_policy()  # should not raise


# ---------------------------------------------------------------------------
# DockerSandboxRuntime._sbx_template_ls and _sbx_template_loaded
# ---------------------------------------------------------------------------

class TestSbxTemplateLoaded:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_sbx_template_ls_parses_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                "REPOSITORY                                    TAG        IMAGE ID    FLAVOR\n"
                "docker.io/library/agent-loop-sandbox-claude   v01d50ddb  542afdc3e   claude-code\n"
                "docker.io/docker/sandbox-templates            claude-code 9a3bab17  claude-code\n"
            )
        )
        templates = DockerSandboxRuntime._sbx_template_ls()
        assert len(templates) == 2
        assert ("docker.io/library/agent-loop-sandbox-claude", "v01d50ddb") in templates

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_sbx_template_ls_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert DockerSandboxRuntime._sbx_template_ls() == []

    @patch.object(DockerSandboxRuntime, "_sbx_template_ls")
    def test_loaded_when_tag_present(self, mock_ls):
        mock_ls.return_value = [
            ("docker.io/library/agent-loop-sandbox-claude", "v01d50ddb"),
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        assert sb._sbx_template_loaded("agent-loop-sandbox-claude:v01d50ddb") is True

    @patch.object(DockerSandboxRuntime, "_sbx_template_ls")
    def test_not_loaded_when_tag_absent(self, mock_ls):
        mock_ls.return_value = [
            ("docker.io/docker/sandbox-templates", "claude-code"),
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        assert sb._sbx_template_loaded("agent-loop-sandbox-claude:v01d50ddb") is False

    @patch.object(DockerSandboxRuntime, "_sbx_template_ls")
    def test_not_loaded_when_store_empty(self, mock_ls):
        mock_ls.return_value = []
        sb = DockerSandboxRuntime("/dotfiles")
        assert sb._sbx_template_loaded("agent-loop-sandbox-claude:v01d50ddb") is False


# ---------------------------------------------------------------------------
# DockerSandboxRuntime._ensure_template_loaded
# ---------------------------------------------------------------------------

class TestEnsureTemplateLoaded:
    @patch.object(DockerSandboxRuntime, "_sbx_template_loaded", return_value=True)
    def test_skips_when_already_loaded(self, mock_loaded):
        sb = DockerSandboxRuntime("/dotfiles")
        with patch("ralph.runtime.docker_sandbox.subprocess.run") as mock_run:
            sb._ensure_template_loaded("agent-loop-sandbox-claude:v01d50ddb")
            mock_run.assert_not_called()

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch.object(DockerSandboxRuntime, "_sbx_template_loaded", return_value=False)
    def test_saves_and_loads_when_not_present(self, mock_loaded, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        sb = DockerSandboxRuntime("/dotfiles")
        sb._ensure_template_loaded("agent-loop-sandbox-claude:v01d50ddb")
        calls = mock_run.call_args_list
        save_calls = [c for c in calls if c[0][0][0] == "docker" and "save" in c[0][0]]
        load_calls = [c for c in calls if c[0][0][0] == "sbx" and "load" in c[0][0]]
        assert len(save_calls) == 1
        assert len(load_calls) == 1
        assert "agent-loop-sandbox-claude:v01d50ddb" in save_calls[0][0][0]


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.cleanup_sandbox
# ---------------------------------------------------------------------------

class TestSandboxCleanup:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_removes_sandbox(self, mock_run):
        DockerSandboxRuntime("/dotfiles").cleanup_sandbox("claude", "fix-auth")
        mock_run.assert_called_once_with(
            ["sbx", "rm", "--force", "agent-loop-claude-fix-auth"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.prune_sandboxes (mocked sbx + filesystem)
# ---------------------------------------------------------------------------

class TestSandboxPruneSandboxes:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_ls")
    def test_removes_orphans(self, mock_ls, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()
        mock_ls.return_value = {
            "sandboxes": [
                {"name": "agent-loop-claude-active", "workspaces": [str(existing)]},
                {"name": "agent-loop-claude-orphan", "workspaces": ["/nonexistent/path"]},
            ]
        }
        sb = DockerSandboxRuntime(str(tmp_path))
        with patch.object(sb, "_sandbox_last_used",
                          side_effect=lambda n: time.time() if "active" in n else None):
            pruned = sb.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-orphan"]
        mock_run.assert_called_once_with(
            ["sbx", "rm", "--force", "agent-loop-claude-orphan"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch.object(DockerSandboxRuntime, "_docker_sandbox_ls")
    def test_keeps_active_sandboxes(self, mock_ls, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()
        mock_ls.return_value = {
            "sandboxes": [
                {"name": "agent-loop-claude-active", "workspaces": [str(existing)]},
            ]
        }
        sb = DockerSandboxRuntime(str(tmp_path))
        with patch.object(sb, "_sandbox_last_used", return_value=time.time()):
            pruned = sb.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()

    @patch.object(DockerSandboxRuntime, "_docker_sandbox_ls")
    def test_ignores_other_agents(self, mock_ls, tmp_path):
        mock_ls.return_value = {
            "sandboxes": [
                {"name": "agent-loop-codex-orphan", "workspaces": ["/nonexistent"]},
            ]
        }
        sb = DockerSandboxRuntime(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []

    @patch.object(DockerSandboxRuntime, "_docker_sandbox_ls")
    def test_empty_sandbox_list(self, mock_ls, tmp_path):
        mock_ls.return_value = {"sandboxes": []}
        sb = DockerSandboxRuntime(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []


# ---------------------------------------------------------------------------
# DockerSandboxRuntime._docker_sandbox_ls (mocked subprocess)
# ---------------------------------------------------------------------------

class TestDockerSandboxRuntimeLs:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_parses_json_output(self, mock_run):
        sandboxes_data = {"sandboxes": [{"name": "test-vm", "workspaces": ["/tmp/w"]}]}
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(sandboxes_data))
        result = DockerSandboxRuntime._docker_sandbox_ls()
        assert result == sandboxes_data

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_calls_sbx_ls_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='{"sandboxes":[]}')
        DockerSandboxRuntime._docker_sandbox_ls()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["sbx", "ls", "--json"]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = DockerSandboxRuntime._docker_sandbox_ls()
        assert result == {"sandboxes": []}

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_returns_empty_on_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = DockerSandboxRuntime._docker_sandbox_ls()
        assert result == {"sandboxes": []}


# ---------------------------------------------------------------------------
# DockerSandboxRuntime._docker_sandbox_create (mocked subprocess)
# ---------------------------------------------------------------------------

class TestDockerSandboxRuntimeCreate:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_claude_uses_claude_subcommand(self, mock_run):
        DockerSandboxRuntime._docker_sandbox_create(
            "my-sandbox", "img:v1", "/work", sandbox_agent="claude")
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "sbx", "create",
            "--name", "my-sandbox", "-t", "img:v1", "claude", "/work"]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_cursor_uses_shell_subcommand(self, mock_run):
        DockerSandboxRuntime._docker_sandbox_create(
            "my-sandbox", "img:v1", "/work", sandbox_agent="shell")
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "sbx", "create",
            "--name", "my-sandbox", "-t", "img:v1", "shell", "/work"]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_defaults_to_claude(self, mock_run):
        DockerSandboxRuntime._docker_sandbox_create("my-sandbox", "img:v1", "/work")
        cmd = mock_run.call_args[0][0]
        assert "claude" in cmd

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_includes_git_common_dir(self, mock_run):
        DockerSandboxRuntime._docker_sandbox_create(
            "my-sandbox", "img:v1", "/work", "/repo/.git",
            sandbox_agent="shell")
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "sbx", "create",
            "--name", "my-sandbox", "-t", "img:v1", "shell",
            "/work", "/repo/.git"]


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.preflight_check (mocked token, proxy, sbx)
# ---------------------------------------------------------------------------

class TestSandboxPreflightCheck:
    SANDBOX_NAME = "agent-loop-claude-fix-auth"

    @staticmethod
    def _run_side_effect(echo_rc=0, curl_rc=28):
        """Create a subprocess.run side_effect for sandbox exec calls."""
        def fn(cmd, **kwargs):
            if "echo" in cmd:
                return MagicMock(returncode=echo_rc, stdout="ok\n", stderr="")
            if "curl" in cmd:
                return MagicMock(returncode=curl_rc, stdout="", stderr="")
            return MagicMock(returncode=0)
        return fn

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(True, "abc123", "oauth", "::"))
    @patch("ralph.runtime.read_token_from_keychain")
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert failures == []

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(True, "abc123", "oauth", "::"))
    @patch("ralph.runtime.read_token_from_keychain", return_value=None)
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_token_missing_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "no token found" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(True, "abc123", "oauth", "::"))
    @patch("ralph.runtime.read_token_from_keychain")
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_token_expired_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "token expired" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(False, None, None, None))
    @patch("ralph.runtime.read_token_from_keychain")
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_proxy_down_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "proxy not reachable" in failures[0]
        assert "start the credential proxy" in failures[0]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(True, "abc123", "oauth", "::"))
    @patch("ralph.runtime.read_token_from_keychain")
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "not responding" in failures[0]
        assert f"sbx rm --force {self.SANDBOX_NAME}" in failures[0]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(True, "abc123", "oauth", "::"))
    @patch("ralph.runtime.read_token_from_keychain")
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_skips_network_check(self, mock_time, mock_read, mock_health, mock_run):
        """When sandbox is unresponsive, network policy check is skipped."""
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=0)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "not responding" in failures[0]
        curl_calls = [c for c in mock_run.call_args_list if "curl" in c[0][0]]
        assert len(curl_calls) == 0

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(True, "abc123", "oauth", "::"))
    @patch("ralph.runtime.read_token_from_keychain")
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_network_policy_not_applied_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=0)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "network policy not applied" in failures[0]
        assert "outbound requests should be blocked" in failures[0]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    @patch("ralph.runtime.proxy_health_check",
           return_value=(False, None, None, None))
    @patch("ralph.runtime.read_token_from_keychain", return_value=None)
    @patch("ralph.runtime.time.time", return_value=1700000000.0)
    def test_multiple_failures_collected(self, mock_time, mock_read, mock_health, mock_run):
        """All failures are collected, not just the first one."""
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = DockerSandboxRuntime("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        # token missing + proxy down + sandbox unresponsive = 3 failures
        assert len(failures) == 3


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.setup_git_config (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxSetupGitConfig:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_sets_user_name_email_and_safe_directory(self, mock_run):
        DockerSandboxRuntime("/dotfiles").setup_git_config("my-sandbox", "Ralph", "ralph@test.com")
        assert mock_run.call_count == 3

        name_call = mock_run.call_args_list[0]
        assert name_call[0][0] == [
            "sbx", "exec", "my-sandbox",
            "git", "config", "--global", "user.name", "Ralph",
        ]

        email_call = mock_run.call_args_list[1]
        assert email_call[0][0] == [
            "sbx", "exec", "my-sandbox",
            "git", "config", "--global", "user.email", "ralph@test.com",
        ]

        safe_call = mock_run.call_args_list[2]
        assert safe_call[0][0] == [
            "sbx", "exec", "my-sandbox",
            "git", "config", "--global", "--add", "safe.directory", "*",
        ]


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.run_iteration (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxRunIteration:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_writes_spec_runs_claude_reads_back(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=0, stdout="updated spec"),  # read spec
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration("my-sandbox", "original spec", "sonnet")
        assert rc == 0
        assert updated == "updated spec"

        # Verify write call pipes spec content via tee
        write_call = mock_run.call_args_list[0]
        assert write_call[1]["input"] == "original spec"
        assert "tee" in write_call[0][0]
        assert "/tmp/spec.md" in write_call[0][0]

        # Verify claude call uses sbx exec with stdin=DEVNULL (avoids sbx
        # 3-second wait warning when no stdin is piped)
        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert cmd[0] == "sbx"
        assert cmd[1] == "exec"
        assert "-w" in cmd
        assert cmd[cmd.index("-w") + 1] == "/work/tree"
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"
        assert "--dangerously-skip-permissions" in cmd
        assert "--effort" in cmd
        assert cmd[cmd.index("-p") + 1] == \
            DockerSandboxRuntime.iteration_prompt("/tmp/spec.md")
        assert claude_call[1].get("stdin") == subprocess.DEVNULL

        # Verify read-back call
        read_call = mock_run.call_args_list[2]
        assert "cat" in read_call[0][0]
        assert "/tmp/spec.md" in read_call[0][0]

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_passes_env_vars_to_claude(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/tree"
        sb.run_iteration("my-sandbox", "spec", "sonnet",
                         env_vars={"CLAUDE_CODE_OAUTH_TOKEN": "sk-test"})

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert "-e" in cmd
        e_idx = cmd.index("-e")
        assert cmd[e_idx + 1] == "CLAUDE_CODE_OAUTH_TOKEN=sk-test"

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_returns_original_spec_on_write_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        sb = DockerSandboxRuntime("/dotfiles")
        rc, updated = sb.run_iteration("my-sandbox", "original", "sonnet")
        assert rc == 1
        assert updated == "original"
        assert mock_run.call_count == 1

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_returns_original_spec_on_read_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=1, stdout=""),  # read spec fails
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration("my-sandbox", "original", "sonnet")
        assert rc == 0
        assert updated == "original"

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_returns_claude_exit_code(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=42),  # claude fails
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, _ = sb.run_iteration("my-sandbox", "spec", "sonnet")
        assert rc == 42

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_cursor_writes_secret_file_and_uses_shell_wrapper(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # write api key
            MagicMock(returncode=0),  # run cursor-agent via sh -c
            MagicMock(returncode=0, stdout="updated spec"),  # read spec
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration(
            "my-sandbox", "original spec", "auto",
            agent="cursor", api_key="test-api-key-123")
        assert rc == 0
        assert updated == "updated spec"

        key_call = mock_run.call_args_list[1]
        assert key_call[1]["input"] == "test-api-key-123"
        assert "tee" in key_call[0][0]
        assert "/tmp/.agent-api-key" in key_call[0][0]

        agent_call = mock_run.call_args_list[2]
        cmd = agent_call[0][0]
        assert "sh" in cmd
        assert "-c" in cmd
        inner = cmd[cmd.index("-c") + 1]
        assert 'CURSOR_API_KEY="$(cat /tmp/.agent-api-key)"' in inner
        assert "rm /tmp/.agent-api-key" in inner
        assert "exec cursor-agent" in inner
        assert "--model auto" in inner
        assert "--force" in inner
        assert "--trust" in inner
        assert "--output-format text" in inner
        assert shlex.quote(
            DockerSandboxRuntime.iteration_prompt("/tmp/spec.md")) in inner

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_cursor_no_env_vars_in_exec(self, mock_run):
        """Cursor agent should not pass env vars via exec -e flags."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # write api key
            MagicMock(returncode=0),  # run cursor-agent
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/tree"
        sb.run_iteration("my-sandbox", "spec", "auto",
                         agent="cursor", api_key="key123")

        agent_call = mock_run.call_args_list[2]
        cmd = agent_call[0][0]
        assert "-e" not in cmd

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_cursor_returns_original_spec_on_key_write_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=1),  # write api key fails
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration(
            "my-sandbox", "original", "auto",
            agent="cursor", api_key="key123")
        assert rc == 1
        assert updated == "original"
        assert mock_run.call_count == 2

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_cursor_workdir_set_in_exec(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # write api key
            MagicMock(returncode=0),  # run cursor-agent
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandboxRuntime("/dotfiles")
        sb._worktree_path = "/work/my-project"
        sb.run_iteration("my-sandbox", "spec", "auto",
                         agent="cursor", api_key="key")

        agent_call = mock_run.call_args_list[2]
        cmd = agent_call[0][0]
        assert "-w" in cmd
        assert cmd[cmd.index("-w") + 1] == "/work/my-project"


# ---------------------------------------------------------------------------
# DockerSandboxRuntime.sync_to_host
# ---------------------------------------------------------------------------

class TestSandboxSyncToHost:
    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_returns_true_when_host_can_see_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = DockerSandboxRuntime("/dotfiles").sync_to_host("sandbox", "abc123", "def456", "/work")
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert "rev-parse" in cmd
        assert "--verify" in cmd
        assert "def456" in cmd

    @patch("ralph.runtime.docker_sandbox.subprocess.run")
    def test_returns_false_when_host_cannot_see_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = DockerSandboxRuntime("/dotfiles").sync_to_host("sandbox", "abc", "def", "/work")
        assert result is False


# ---------------------------------------------------------------------------
# Runtime.iteration_prompt
# ---------------------------------------------------------------------------

# The iteration prompt exactly as it read before the spec path became a
# template parameter.  Rendering the template with the historical
# "/tmp/spec.md" path must still reproduce it byte for byte.
PREVIOUS_ITERATION_PROMPT = """\
You are an AI coding agent. You will be invoked repeatedly — once per task.
Read the spec file at `/tmp/spec.md` for what to build.

Your job this iteration: implement EXACTLY ONE task, then stop.

Steps:
1. Study the spec and existing codebase (especially CLAUDE.md) to understand patterns
2. Check git log to see what has already been implemented
3. Pick the FIRST incomplete task from the spec
4. Implement that single task fully — no stubs or placeholders

If ALL tasks are already complete, just say so — do not make any commits.

Rules:
- Follow conventions in CLAUDE.md if it exists
- Search the codebase before assuming something isn't implemented
- NEVER run `git init`. If git commands fail in the workspace, the sandbox runtime is misconfigured. Stop immediately and report the error — do not attempt to fix git yourself.

For each task, follow this workflow:

1. **Implement** — Write the code described in the task
2. **Test** — Write tests that cover the task's Acceptance criteria
3. **Verify** — Run tests and any commands listed in Acceptance. Fix failures until all pass.
4. **Self-review** — Review your changes from a fresh perspective, as if you are a different developer seeing this code for the first time. Look at the full diff of your changes and check for:
   - Bugs, off-by-one errors, edge cases
   - Logic errors or missed requirements from the spec
   - Adherence to project conventions (CLAUDE.md, existing patterns)
   - Security issues, resource leaks
   If using Claude Code, use the Agent tool to spawn a "feature-dev:code-reviewer" subagent for this step — a fresh context catches things you will miss.
   Fix any issues found, re-run tests, and re-review if changes were substantial.
5. **Commit** — Stage and commit your changes with a clear message
6. **Update spec** — Mark the step `[done]` and record any decisions or deviations

IMPORTANT: Do NOT implement more than one task. One task, one commit, then stop. The loop will call you again for the next task.

Spec maintenance rules:
- Mark each step `[done]` when complete.
- Record design decisions that emerged during implementation as notes under the step.
- Minor deviations (e.g. flag name changes, reordered logic) should be noted and the spec updated to match.
- Significant design changes (e.g. new subcommands, changed architecture, removed features) require pausing for user review before proceeding.

Unfulfillable tasks:
- If a task cannot be completed because required tools or infrastructure are unavailable (e.g., test runner not installed, build tool missing, external service unreachable), append `[blocked: <reason>]` to the step heading line (e.g., `### Step 3: Run tests [blocked: pytest not installed]`) and do NOT commit. The outer loop will detect this marker and transition the issue to `status:needs-attention`.

Run all checks:
- The 'Run all checks' step (typically the final step) must ALWAYS execute the full test suite, linter, and syntax checks — even if earlier steps already ran individual tests. This step catches cross-cutting regressions. Never skip it or mark it done without actually running the checks."""


class TestIterationPrompt:
    """Verify the rendered iteration prompt is unchanged and substitutable."""

    def test_renders_previous_prompt_text_exactly(self):
        assert Runtime.iteration_prompt("/tmp/spec.md") == PREVIOUS_ITERATION_PROMPT

    @pytest.mark.parametrize("runtime_cls", [
        Runtime, DockerSandboxRuntime, DockerContainerRuntime, TartRuntime,
    ])
    def test_every_runtime_renders_the_same_prompt(self, runtime_cls):
        assert runtime_cls.iteration_prompt("/tmp/spec.md") == PREVIOUS_ITERATION_PROMPT

    def test_substitutes_the_spec_path(self):
        prompt = Runtime.iteration_prompt("/home/u/.ralph/nono/sb/spec.ab12cd34")
        assert "Read the spec file at `/home/u/.ralph/nono/sb/spec.ab12cd34`" in prompt
        assert "/tmp/spec.md" not in prompt

    def test_template_has_no_unescaped_braces(self):
        """format() must not choke on, or silently eat, stray braces."""
        rendered = Runtime.iteration_prompt("SPEC")
        assert "{" not in rendered and "}" not in rendered

    def test_contains_blocked_marker_rule(self):
        assert "[blocked:" in Runtime.iteration_prompt("/tmp/spec.md")

    def test_contains_run_all_checks_rule(self):
        assert "Run all checks" in Runtime.iteration_prompt("/tmp/spec.md")

    def test_contains_spec_maintenance_rules(self):
        assert "Spec maintenance rules" in Runtime.iteration_prompt("/tmp/spec.md")

    def test_contains_step_structure(self):
        assert "For each task, follow this workflow" in Runtime.iteration_prompt("/tmp/spec.md")

    def test_contains_unfulfillable_tasks_section(self):
        assert "Unfulfillable tasks" in Runtime.iteration_prompt("/tmp/spec.md")
