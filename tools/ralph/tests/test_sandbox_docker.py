"""Unit tests for ralph.sandbox.docker — DockerSandbox backend."""

import datetime
import json
import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from ralph.sandbox import SandboxBackend, load_sandbox_config, create_sandbox_backend
from ralph.sandbox.docker import DockerSandbox


# ---------------------------------------------------------------------------
# load_sandbox_config
# ---------------------------------------------------------------------------

class TestLoadSandboxConfig:
    def test_default_when_no_config(self, tmp_path):
        """No .agent-loop/config.json returns docker default."""
        assert load_sandbox_config(str(tmp_path)) == {"type": "docker"}

    def test_default_when_no_agent_loop_dir(self, tmp_path):
        """No .agent-loop directory at all returns docker default."""
        assert load_sandbox_config(str(tmp_path)) == {"type": "docker"}

    def test_docker_type(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "docker"}')
        assert load_sandbox_config(str(tmp_path)) == {"type": "docker"}

    def test_tart_type_with_base_image(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {
            "type": "tart",
            "base_image": "ghcr.io/cirruslabs/macos-sequoia-xcode:latest",
        }
        (config_dir / "config.json").write_text(json.dumps(config))
        result = load_sandbox_config(str(tmp_path))
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
        result = load_sandbox_config(str(tmp_path))
        assert result["type"] == "tart"
        assert result["cpu"] == 4
        assert result["memory_gb"] == 8

    def test_missing_type_defaults_to_docker(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"some_key": "value"}')
        result = load_sandbox_config(str(tmp_path))
        assert result["type"] == "docker"

    def test_unknown_type_raises_value_error(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text('{"type": "kubernetes"}')
        with pytest.raises(ValueError, match="unknown sandbox type"):
            load_sandbox_config(str(tmp_path))

    def test_allowed_hosts_passed_through(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        config = {"type": "docker", "allowed_hosts": ["pypi.org", "example.com"]}
        (config_dir / "config.json").write_text(json.dumps(config))
        result = load_sandbox_config(str(tmp_path))
        assert result["allowed_hosts"] == ["pypi.org", "example.com"]

    def test_malformed_json_raises(self, tmp_path):
        config_dir = tmp_path / ".agent-loop"
        config_dir.mkdir()
        (config_dir / "config.json").write_text("{not valid json")
        with pytest.raises(json.JSONDecodeError):
            load_sandbox_config(str(tmp_path))


# ---------------------------------------------------------------------------
# SandboxBackend
# ---------------------------------------------------------------------------

class TestSandboxBackend:
    """SandboxBackend interface methods raise NotImplementedError."""

    def test_proxy_host_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().proxy_host()

    def test_ensure_image_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().ensure_image("claude")

    def test_ensure_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().ensure_sandbox("claude", "main", "/work")

    def test_setup_git_config_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().setup_git_config("name", "user", "email")

    def test_run_iteration_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().run_iteration("name", "spec", "model")

    def test_preflight_backend_checks_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend()._preflight_backend_checks("name")

    def test_cleanup_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().cleanup_sandbox("agent", "branch")

    def test_prune_sandboxes_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().prune_sandboxes("agent")

    def test_remove_sandbox_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().remove_sandbox("name")

    def test_check_prerequisites_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().check_prerequisites()

    def test_check_in_sync_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().check_in_sync("name", "/work", None)

    def test_reset_to_host_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().reset_to_host("name", "/work", None)

    def test_sync_to_host_raises(self):
        with pytest.raises(NotImplementedError):
            SandboxBackend().sync_to_host("name", "abc", "def", "/work")

    def test_sandbox_name_shared(self):
        """sandbox_name is shared logic, not abstract."""
        assert SandboxBackend.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"

    def test_docker_sandbox_inherits_sandbox_name(self):
        """DockerSandbox inherits sandbox_name from SandboxBackend."""
        assert DockerSandbox.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"


# ---------------------------------------------------------------------------
# create_sandbox_backend factory
# ---------------------------------------------------------------------------

class TestCreateSandboxBackend:
    def test_docker_returns_docker_sandbox(self):
        backend = create_sandbox_backend("docker", "/dotfiles")
        assert isinstance(backend, DockerSandbox)
        assert backend.dotfiles_dir == "/dotfiles"

    def test_tart_returns_tart_sandbox(self):
        from ralph.sandbox.tart import TartSandbox
        backend = create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="ghcr.io/cirruslabs/macos-sequoia-xcode:latest")
        assert isinstance(backend, TartSandbox)
        assert backend.dotfiles_dir == "/dotfiles"
        assert backend.base_image == "ghcr.io/cirruslabs/macos-sequoia-xcode:latest"

    def test_tart_reads_dependencies_from_project_dir(self, tmp_path):
        agent_loop = tmp_path / ".agent-loop"
        agent_loop.mkdir()
        (agent_loop / "dependencies").write_text("brew install jq\n")
        backend = create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path))
        assert backend.dependencies_content == "brew install jq\n"

    def test_tart_no_dependencies_file(self, tmp_path):
        backend = create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path))
        assert backend.dependencies_content == ""

    def test_tart_explicit_dependencies_not_overridden(self, tmp_path):
        """If dependencies_content is passed explicitly, don't read from file."""
        agent_loop = tmp_path / ".agent-loop"
        agent_loop.mkdir()
        (agent_loop / "dependencies").write_text("from file\n")
        backend = create_sandbox_backend(
            "tart", "/dotfiles",
            base_image="img:latest", project_dir=str(tmp_path),
            dependencies_content="explicit\n")
        assert backend.dependencies_content == "explicit\n"

    def test_unknown_type_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown sandbox type 'podman'"):
            create_sandbox_backend("podman", "/dotfiles")

    def test_kwargs_passed_through(self):
        """Extra kwargs don't break DockerSandbox creation."""
        backend = create_sandbox_backend(
            "docker", "/dotfiles", base_image="foo", cpu=4)
        assert isinstance(backend, DockerSandbox)

    def test_docker_allowed_hosts_passed_through(self):
        """allowed_hosts from config reaches DockerSandbox."""
        backend = create_sandbox_backend(
            "docker", "/dotfiles",
            allowed_hosts=["pypi.org", "example.com"])
        assert backend.allowed_hosts == ("pypi.org", "example.com")

    def test_docker_no_allowed_hosts_defaults_to_empty(self):
        backend = create_sandbox_backend("docker", "/dotfiles")
        assert backend.allowed_hosts == ()


# ---------------------------------------------------------------------------
# DockerSandbox.parse_base_image
# ---------------------------------------------------------------------------

class TestSandboxParseBaseImage:
    def test_extracts_from_line(self):
        content = "FROM docker/sandbox-templates:claude-code\nUSER root"
        assert DockerSandbox.parse_base_image(content) == "docker/sandbox-templates:claude-code"

    def test_returns_none_when_no_from(self):
        assert DockerSandbox.parse_base_image("RUN echo hi") is None

    def test_ignores_comment_lines(self):
        content = "# FROM fake:image\nFROM real:latest"
        assert DockerSandbox.parse_base_image(content) == "real:latest"

    def test_returns_final_stage_in_multistage(self):
        content = "FROM builder:latest AS build\nRUN make\nFROM runtime:slim"
        assert DockerSandbox.parse_base_image(content) == "runtime:slim"


# ---------------------------------------------------------------------------
# DockerSandbox.content_hash
# ---------------------------------------------------------------------------

class TestSandboxContentHash:
    def test_deterministic(self):
        h1 = DockerSandbox.content_hash("FROM a", "digest1")
        h2 = DockerSandbox.content_hash("FROM a", "digest1")
        assert h1 == h2

    def test_changes_when_dockerfile_changes(self):
        h1 = DockerSandbox.content_hash("FROM a\nRUN echo old", "digest1")
        h2 = DockerSandbox.content_hash("FROM a\nRUN echo new", "digest1")
        assert h1 != h2

    def test_changes_when_base_digest_changes(self):
        df = "FROM a\nRUN echo same"
        h1 = DockerSandbox.content_hash(df, "sha256:aaa")
        h2 = DockerSandbox.content_hash(df, "sha256:bbb")
        assert h1 != h2

    def test_length_is_8(self):
        h = DockerSandbox.content_hash("FROM a", "d")
        assert len(h) == 8


# ---------------------------------------------------------------------------
# DockerSandbox.image_tag
# ---------------------------------------------------------------------------

class TestSandboxImageTag:
    def test_format(self):
        tag = DockerSandbox.image_tag("claude", "abc123de")
        assert tag == "agent-loop-sandbox-claude:vabc123de"

    def test_custom_agent(self):
        tag = DockerSandbox.image_tag("codex", "xyz")
        assert tag == "agent-loop-sandbox-codex:vxyz"


# ---------------------------------------------------------------------------
# DockerSandbox.parse_dependencies
# ---------------------------------------------------------------------------

class TestSandboxParseDependencies:
    def test_basic_package_list(self):
        content = "openjdk-21-jdk\npython3-venv\nnodejs"
        assert DockerSandbox.parse_dependencies(content) == [
            "openjdk-21-jdk", "python3-venv", "nodejs"
        ]

    def test_comment_only_lines_skipped(self):
        content = "# This is a comment\npkg1\n# Another comment\npkg2"
        assert DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_inline_comments_stripped(self):
        content = "pkg1 # this is a comment\npkg2 # another"
        assert DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2"]

    def test_blank_lines_skipped(self):
        content = "pkg1\n\n\npkg2\n\npkg3"
        assert DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_whitespace_handling(self):
        content = "  pkg1  \n\tpkg2\t\n  pkg3  # comment  "
        assert DockerSandbox.parse_dependencies(content) == ["pkg1", "pkg2", "pkg3"]

    def test_empty_content(self):
        assert DockerSandbox.parse_dependencies("") == []

    def test_only_comments_and_blanks(self):
        content = "# comment\n\n# another\n  \n"
        assert DockerSandbox.parse_dependencies(content) == []

    def test_rejects_shell_injection(self):
        with pytest.raises(ValueError, match="invalid package name"):
            DockerSandbox.parse_dependencies("pkg; rm -rf /")

    def test_rejects_uppercase_names(self):
        with pytest.raises(ValueError, match="invalid package name"):
            DockerSandbox.parse_dependencies("BadPkg")

    def test_accepts_arch_qualifier(self):
        result = DockerSandbox.parse_dependencies("libc6:amd64")
        assert result == ["libc6:amd64"]

    def test_accepts_version_pinning(self):
        result = DockerSandbox.parse_dependencies("openjdk-21-jdk=21.0.1+12-1")
        assert result == ["openjdk-21-jdk=21.0.1+12-1"]


# ---------------------------------------------------------------------------
# DockerSandbox.generate_project_dockerfile
# ---------------------------------------------------------------------------

class TestSandboxGenerateProjectDockerfile:
    def test_single_package(self):
        result = DockerSandbox.generate_project_dockerfile(["openjdk-21-jdk"])
        assert "apt-get install -y --no-install-recommends" in result
        assert "openjdk-21-jdk" in result

    def test_multiple_packages_joined(self):
        result = DockerSandbox.generate_project_dockerfile(["pkg1", "pkg2", "pkg3"])
        assert "'pkg1' 'pkg2' 'pkg3'" in result

    def test_packages_are_shell_quoted(self):
        result = DockerSandbox.generate_project_dockerfile(["pkg; rm -rf /"])
        assert "\"pkg; rm -rf /\"" not in result
        assert "'pkg; rm -rf /'" in result

    def test_contains_arg_and_from(self):
        result = DockerSandbox.generate_project_dockerfile(["pkg1"])
        assert "ARG BASE_IMAGE" in result
        assert "FROM ${BASE_IMAGE}" in result

    def test_user_switching(self):
        result = DockerSandbox.generate_project_dockerfile(["pkg1"])
        lines = result.splitlines()
        assert "USER root" in lines
        assert "USER agent" in lines
        assert lines.index("USER root") < lines.index("USER agent")

    def test_apt_cleanup(self):
        result = DockerSandbox.generate_project_dockerfile(["pkg1"])
        assert "rm -rf /var/lib/apt/lists/*" in result

    def test_no_install_recommends(self):
        result = DockerSandbox.generate_project_dockerfile(["pkg1"])
        assert "--no-install-recommends" in result


# ---------------------------------------------------------------------------
# DockerSandbox.find_project_config
# ---------------------------------------------------------------------------

class TestSandboxFindProjectConfig:
    def test_returns_none_when_no_agent_loop_dir(self, tmp_path):
        result = DockerSandbox.find_project_config(str(tmp_path))
        assert result is None

    def test_returns_none_when_agent_loop_empty(self, tmp_path):
        (tmp_path / ".agent-loop").mkdir()
        result = DockerSandbox.find_project_config(str(tmp_path))
        assert result is None

    def test_prefers_dockerfile_over_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = DockerSandbox.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")

    def test_returns_dependencies_when_no_dockerfile(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "dependencies").write_text("pkg1\n")
        config_type, path = DockerSandbox.find_project_config(str(tmp_path))
        assert config_type == "dependencies"
        assert path == str(al / "dependencies")

    def test_returns_dockerfile_when_no_dependencies(self, tmp_path):
        al = tmp_path / ".agent-loop"
        al.mkdir()
        (al / "Dockerfile.sandbox").write_text("FROM base\n")
        config_type, path = DockerSandbox.find_project_config(str(tmp_path))
        assert config_type == "dockerfile"
        assert path == str(al / "Dockerfile.sandbox")


# ---------------------------------------------------------------------------
# DockerSandbox.project_image_tag
# ---------------------------------------------------------------------------

class TestSandboxProjectImageTag:
    def test_includes_agent_and_project_in_tag(self):
        tag = DockerSandbox.project_image_tag("claude", "myproject", "base:v1", "content")
        assert tag.startswith("agent-loop-sandbox-claude-myproject:v")

    def test_hash_is_8_chars(self):
        tag = DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        chash = tag.split(":v")[1]
        assert len(chash) == 8

    def test_different_content_produces_different_hash(self):
        tag1 = DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content-a")
        tag2 = DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content-b")
        assert tag1 != tag2

    def test_same_content_produces_same_hash(self):
        tag1 = DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        assert tag1 == tag2

    def test_different_base_tag_produces_different_hash(self):
        tag1 = DockerSandbox.project_image_tag("claude", "proj", "base:v1", "content")
        tag2 = DockerSandbox.project_image_tag("claude", "proj", "base:v2", "content")
        assert tag1 != tag2


# ---------------------------------------------------------------------------
# DockerSandbox.ensure_project_image
# ---------------------------------------------------------------------------

class TestSandboxEnsureProjectImage:
    @staticmethod
    def _make_sandbox(tmp_path):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text("FROM base:latest\nRUN echo hi")
        return DockerSandbox(str(tmp_path))

    def test_returns_base_tag_when_no_project_config(self, tmp_path):
        sb = self._make_sandbox(tmp_path)
        result = sb.ensure_project_image("claude", "base:v1", str(tmp_path))
        assert result == "base:v1"

    @patch("ralph.sandbox.docker.subprocess.run")
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

    @patch("ralph.sandbox.docker.subprocess.run")
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

    @patch("ralph.sandbox.docker.subprocess.run")
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

    @patch("ralph.sandbox.docker.subprocess.run")
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

    @patch("ralph.sandbox.docker.subprocess.run")
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
# DockerSandbox._parse_docker_timestamp
# ---------------------------------------------------------------------------

class TestSandboxParseDockerTimestamp:
    def test_z_suffix(self):
        dt = DockerSandbox._parse_docker_timestamp("2024-06-15T10:30:00Z")
        assert dt.year == 2024 and dt.month == 6

    def test_truncates_nanoseconds(self):
        dt = DockerSandbox._parse_docker_timestamp("2024-06-15T10:30:00.123456789Z")
        assert dt.microsecond == 123456

    def test_offset_format(self):
        dt = DockerSandbox._parse_docker_timestamp("2024-06-15T10:30:00+00:00")
        assert dt.year == 2024


# ---------------------------------------------------------------------------
# DockerSandbox.needs_rebuild
# ---------------------------------------------------------------------------

class TestSandboxNeedsRebuild:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return DockerSandbox(str(tmp_path))

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_true_when_image_missing(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=1)
        assert sb.needs_rebuild("claude") is True

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_true_when_image_old(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="2020-01-01T00:00:00Z\n")
        assert sb.needs_rebuild("claude") is True

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_false_when_image_recent(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.return_value = MagicMock(returncode=0, stdout="2099-01-01T00:00:00Z\n")
        assert sb.needs_rebuild("claude") is False


# ---------------------------------------------------------------------------
# DockerSandbox.ensure_image
# ---------------------------------------------------------------------------

class TestSandboxEnsureImage:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return DockerSandbox(str(tmp_path))

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

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_skips_build_when_tag_exists(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=True)
        tag = sb.ensure_image("claude")
        assert tag.startswith("agent-loop-sandbox-claude:v")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 0

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_builds_when_tag_missing(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(image_exists=False)
        sb.ensure_image("claude")
        build_calls = [c for c in mock_run.call_args_list
                       if c[0][0][1] == "build"]
        assert len(build_calls) == 1

    @patch("ralph.sandbox.docker.subprocess.run")
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

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_pulls_when_base_image_stale(self, mock_run, tmp_path):
        sb = self._make_sandbox(tmp_path)
        mock_run.side_effect = self._side_effect(
            image_exists=False, base_age="2020-01-01T00:00:00Z")
        sb.ensure_image("claude")
        pull_calls = [c for c in mock_run.call_args_list
                      if c[0][0][1] == "pull"]
        assert len(pull_calls) == 1


# ---------------------------------------------------------------------------
# SandboxBackend.sandbox_name
# ---------------------------------------------------------------------------

class TestSandboxName:
    def test_simple_branch(self):
        assert DockerSandbox.sandbox_name("claude", "fix-auth") == "agent-loop-claude-fix-auth"

    def test_branch_with_slashes(self):
        assert DockerSandbox.sandbox_name("claude", "feature/foo") == "agent-loop-claude-feature-foo"

    def test_branch_with_multiple_slashes(self):
        assert DockerSandbox.sandbox_name("claude", "user/feature/bar") == "agent-loop-claude-user-feature-bar"

    def test_branch_uppercase_lowered(self):
        assert DockerSandbox.sandbox_name("claude", "Fix-Auth") == "agent-loop-claude-fix-auth"

    def test_consecutive_slashes_collapsed(self):
        assert DockerSandbox.sandbox_name("claude", "a//b") == "agent-loop-claude-a-b"

    def test_leading_trailing_hyphens_stripped(self):
        assert DockerSandbox.sandbox_name("claude", "-branch-") == "agent-loop-claude-branch"

    def test_custom_agent(self):
        assert DockerSandbox.sandbox_name("codex", "my-branch") == "agent-loop-codex-my-branch"


# ---------------------------------------------------------------------------
# DockerSandbox.ensure_sandbox (mocked docker)
# ---------------------------------------------------------------------------

class TestSandboxEnsureSandbox:
    @staticmethod
    def _make_sandbox(tmp_path, dockerfile="FROM base:latest\nRUN echo hi"):
        agent_dir = tmp_path / "docker" / "agent-loop" / "claude"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "Dockerfile").write_text(dockerfile)
        return DockerSandbox(str(tmp_path))

    @patch.object(DockerSandbox, "apply_network_policy")
    @patch.object(DockerSandbox, "_docker_sandbox_create")
    @patch.object(DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandbox, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandbox, "sandbox_exists", return_value=False)
    def test_creates_new_sandbox(self, mock_exists, mock_img, mock_resolve,
                                 mock_create, mock_policy):
        sb = DockerSandbox("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth", "agent-loop-sandbox-claude:vabc",
            "/work/fix-auth", "/repo/.git", sandbox_agent="claude")
        mock_policy.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            ["api.anthropic.com", "statsig.anthropic.com", "sentry.io"])

    @patch.object(DockerSandbox, "sandbox_exists", return_value=True)
    def test_reuses_existing_sandbox(self, mock_exists):
        sb = DockerSandbox("/dotfiles")
        name = sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-claude-fix-auth"

    @patch.object(DockerSandbox, "apply_network_policy")
    @patch.object(DockerSandbox, "_docker_sandbox_create")
    @patch.object(DockerSandbox, "ensure_image", return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandbox, "sandbox_exists", return_value=True)
    def test_reuse_skips_create_and_policy(self, mock_exists, mock_img, mock_create, mock_policy):
        sb = DockerSandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
        mock_create.assert_not_called()
        mock_policy.assert_not_called()
        mock_img.assert_not_called()

    @patch.object(DockerSandbox, "apply_network_policy")
    @patch.object(DockerSandbox, "_docker_sandbox_create")
    @patch.object(DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandbox, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(DockerSandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandbox, "sandbox_exists", return_value=False)
    def test_calls_ensure_project_image_when_project_dir(
            self, mock_exists, mock_img, mock_proj, mock_resolve,
            mock_create, mock_policy):
        sb = DockerSandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root")
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=False)
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude-myproj:vdef12345",
            "/work/fix-auth", "/repo/.git", sandbox_agent="claude")

    @patch.object(DockerSandbox, "apply_network_policy")
    @patch.object(DockerSandbox, "_docker_sandbox_create")
    @patch.object(DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandbox, "sandbox_exists", return_value=False)
    def test_skips_project_image_when_no_project_dir(
            self, mock_exists, mock_img, mock_resolve, mock_create, mock_policy):
        sb = DockerSandbox("/dotfiles")
        with patch.object(DockerSandbox, "ensure_project_image") as mock_proj:
            sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth")
            mock_proj.assert_not_called()
        mock_create.assert_called_once_with(
            "agent-loop-claude-fix-auth",
            "agent-loop-sandbox-claude:vabc",
            "/work/fix-auth", "/repo/.git", sandbox_agent="claude")

    @patch.object(DockerSandbox, "apply_network_policy")
    @patch.object(DockerSandbox, "_docker_sandbox_create")
    @patch.object(DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandbox, "ensure_project_image",
                  return_value="agent-loop-sandbox-claude-myproj:vdef12345")
    @patch.object(DockerSandbox, "ensure_image",
                  return_value="agent-loop-sandbox-claude:vabc")
    @patch.object(DockerSandbox, "sandbox_exists", return_value=False)
    def test_force_rebuild_passed_through(
            self, mock_exists, mock_img, mock_proj, mock_resolve,
            mock_create, mock_policy):
        sb = DockerSandbox("/dotfiles")
        sb.ensure_sandbox("claude", "fix-auth", "/work/fix-auth",
                          project_dir="/repo/root", force_rebuild=True)
        mock_img.assert_called_once_with("claude", force_rebuild=True)
        mock_proj.assert_called_once_with(
            "claude", "agent-loop-sandbox-claude:vabc", "/repo/root",
            force_rebuild=True)

    @patch.object(DockerSandbox, "apply_network_policy")
    @patch.object(DockerSandbox, "_docker_sandbox_create")
    @patch.object(DockerSandbox, "_resolve_git_common_dir", return_value="/repo/.git")
    @patch.object(DockerSandbox, "ensure_image", return_value="agent-loop-sandbox-cursor:vabc")
    @patch.object(DockerSandbox, "sandbox_exists", return_value=False)
    def test_cursor_uses_shell_sandbox_agent(self, mock_exists, mock_img,
                                             mock_resolve, mock_create,
                                             mock_policy):
        sb = DockerSandbox("/dotfiles")
        name = sb.ensure_sandbox("cursor", "fix-auth", "/work/fix-auth")
        assert name == "agent-loop-cursor-fix-auth"
        mock_create.assert_called_once_with(
            "agent-loop-cursor-fix-auth", "agent-loop-sandbox-cursor:vabc",
            "/work/fix-auth", "/repo/.git", sandbox_agent="shell")
        mock_policy.assert_called_once_with(
            "agent-loop-cursor-fix-auth",
            ["api2.cursor.sh", "api5.cursor.sh", "sentry.io"])


# ---------------------------------------------------------------------------
# DockerSandbox.apply_network_policy
# ---------------------------------------------------------------------------

class TestSandboxApplyNetworkPolicy:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_claude_hosts(self, mock_run):
        allowed = ["api.anthropic.com", "statsig.anthropic.com", "sentry.io"]
        DockerSandbox("/dotfiles").apply_network_policy("agent-loop-claude-fix-auth", allowed)
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "network", "proxy", "agent-loop-claude-fix-auth",
             "--policy", "deny",
             "--allow-host", "localhost",
             "--allow-host", "api.anthropic.com",
             "--allow-host", "statsig.anthropic.com",
             "--allow-host", "sentry.io"],
            check=True,
        )

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_cursor_hosts(self, mock_run):
        allowed = ["api2.cursor.sh", "api5.cursor.sh", "sentry.io"]
        DockerSandbox("/dotfiles").apply_network_policy("agent-loop-cursor-fix-auth", allowed)
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "network", "proxy", "agent-loop-cursor-fix-auth",
             "--policy", "deny",
             "--allow-host", "localhost",
             "--allow-host", "api2.cursor.sh",
             "--allow-host", "api5.cursor.sh",
             "--allow-host", "sentry.io"],
            check=True,
        )

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_extra_allowed_hosts(self, mock_run):
        sb = DockerSandbox("/dotfiles", allowed_hosts=["pypi.org", "registry.npmjs.org"])
        sb.apply_network_policy("agent-loop-claude-fix-auth",
                                ["api.anthropic.com", "statsig.anthropic.com", "sentry.io"])
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "network", "proxy", "agent-loop-claude-fix-auth",
             "--policy", "deny",
             "--allow-host", "localhost",
             "--allow-host", "api.anthropic.com",
             "--allow-host", "statsig.anthropic.com",
             "--allow-host", "sentry.io",
             "--allow-host", "pypi.org",
             "--allow-host", "registry.npmjs.org"],
            check=True,
        )

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_localhost_always_included(self, mock_run):
        DockerSandbox("/dotfiles").apply_network_policy("sandbox", ["example.com"])
        cmd = mock_run.call_args[0][0]
        # localhost should appear before the custom host
        assert "--allow-host" in cmd
        idx = cmd.index("localhost")
        assert cmd[idx - 1] == "--allow-host"


# ---------------------------------------------------------------------------
# DockerSandbox.cleanup_sandbox
# ---------------------------------------------------------------------------

class TestSandboxCleanup:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_removes_sandbox(self, mock_run):
        DockerSandbox("/dotfiles").cleanup_sandbox("claude", "fix-auth")
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "rm", "agent-loop-claude-fix-auth"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )


# ---------------------------------------------------------------------------
# DockerSandbox.prune_sandboxes (mocked docker + filesystem)
# ---------------------------------------------------------------------------

class TestSandboxPruneSandboxes:
    @patch("ralph.sandbox.docker.subprocess.run")
    @patch.object(DockerSandbox, "_docker_sandbox_ls")
    def test_removes_orphans(self, mock_ls, mock_run, tmp_path):
        # Create one workspace that exists
        existing = tmp_path / "workspace"
        existing.mkdir()
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-claude-active", "workspace": str(existing)},
                {"name": "agent-loop-claude-orphan", "workspace": "/nonexistent/path"},
            ]
        }
        sb = DockerSandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == ["agent-loop-claude-orphan"]
        mock_run.assert_called_once_with(
            ["docker", "sandbox", "rm", "agent-loop-claude-orphan"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False,
        )

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch.object(DockerSandbox, "_docker_sandbox_ls")
    def test_keeps_active_sandboxes(self, mock_ls, mock_run, tmp_path):
        existing = tmp_path / "workspace"
        existing.mkdir()
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-claude-active", "workspace": str(existing)},
            ]
        }
        sb = DockerSandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []
        mock_run.assert_not_called()

    @patch.object(DockerSandbox, "_docker_sandbox_ls")
    def test_ignores_other_agents(self, mock_ls, tmp_path):
        mock_ls.return_value = {
            "vms": [
                {"name": "agent-loop-codex-orphan", "workspace": "/nonexistent"},
            ]
        }
        sb = DockerSandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []

    @patch.object(DockerSandbox, "_docker_sandbox_ls")
    def test_empty_vm_list(self, mock_ls, tmp_path):
        mock_ls.return_value = {"vms": []}
        sb = DockerSandbox(str(tmp_path))
        pruned = sb.prune_sandboxes("claude")
        assert pruned == []


# ---------------------------------------------------------------------------
# DockerSandbox._docker_sandbox_ls (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxDockerSandboxLs:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_parses_json_output(self, mock_run):
        vms_data = {"vms": [{"name": "test-vm", "workspace": "/tmp/w"}]}
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps(vms_data))
        result = DockerSandbox._docker_sandbox_ls()
        assert result == vms_data

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = DockerSandbox._docker_sandbox_ls()
        assert result == {"vms": []}

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_returns_empty_on_invalid_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json")
        result = DockerSandbox._docker_sandbox_ls()
        assert result == {"vms": []}


# ---------------------------------------------------------------------------
# DockerSandbox._docker_sandbox_create (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxDockerSandboxCreate:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_claude_uses_claude_subcommand(self, mock_run):
        DockerSandbox._docker_sandbox_create(
            "my-sandbox", "img:v1", "/work", sandbox_agent="claude")
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "docker", "sandbox", "create",
            "--name", "my-sandbox", "-t", "img:v1", "claude", "/work"]

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_cursor_uses_shell_subcommand(self, mock_run):
        DockerSandbox._docker_sandbox_create(
            "my-sandbox", "img:v1", "/work", sandbox_agent="shell")
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "docker", "sandbox", "create",
            "--name", "my-sandbox", "-t", "img:v1", "shell", "/work"]

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_defaults_to_claude(self, mock_run):
        DockerSandbox._docker_sandbox_create("my-sandbox", "img:v1", "/work")
        cmd = mock_run.call_args[0][0]
        assert "claude" in cmd

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_includes_git_common_dir(self, mock_run):
        DockerSandbox._docker_sandbox_create(
            "my-sandbox", "img:v1", "/work", "/repo/.git",
            sandbox_agent="shell")
        cmd = mock_run.call_args[0][0]
        assert cmd == [
            "docker", "sandbox", "create",
            "--name", "my-sandbox", "-t", "img:v1", "shell",
            "/work", "/repo/.git"]


# ---------------------------------------------------------------------------
# DockerSandbox.preflight_check (mocked token, proxy, docker)
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

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.sandbox.read_token_from_keychain")
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_all_checks_pass(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert failures == []

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.sandbox.read_token_from_keychain", return_value=None)
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_token_missing_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "no token found" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.sandbox.read_token_from_keychain")
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_token_expired_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        past_ms = 1700000000000 - 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": past_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "token expired" in failures[0]
        assert "ralph store-token" in failures[0]

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(False, None))
    @patch("ralph.sandbox.read_token_from_keychain")
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_proxy_down_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=28)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "proxy not reachable" in failures[0]
        assert "start the credential proxy" in failures[0]

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.sandbox.read_token_from_keychain")
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "not responding" in failures[0]
        assert f"docker sandbox rm {self.SANDBOX_NAME}" in failures[0]

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.sandbox.read_token_from_keychain")
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_sandbox_unresponsive_skips_network_check(self, mock_time, mock_read, mock_health, mock_run):
        """When sandbox is unresponsive, network policy check is skipped."""
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=0)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        # Should only have sandbox error, not network policy error
        assert len(failures) == 1
        assert "not responding" in failures[0]
        # curl should not have been called
        curl_calls = [c for c in mock_run.call_args_list if "curl" in c[0][0]]
        assert len(curl_calls) == 0

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(True, "abc123"))
    @patch("ralph.sandbox.read_token_from_keychain")
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_network_policy_not_applied_returns_error(self, mock_time, mock_read, mock_health, mock_run):
        future_ms = 1700000000000 + 30 * 86400 * 1000
        mock_read.return_value = {"accessToken": "sk-test", "expiresAt": future_ms}
        # echo succeeds, curl also succeeds (google.com reachable = bad)
        mock_run.side_effect = self._run_side_effect(echo_rc=0, curl_rc=0)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        assert len(failures) == 1
        assert "network policy not applied" in failures[0]
        assert "outbound requests should be blocked" in failures[0]

    @patch("ralph.sandbox.docker.subprocess.run")
    @patch("ralph.sandbox.proxy_health_check", return_value=(False, None))
    @patch("ralph.sandbox.read_token_from_keychain", return_value=None)
    @patch("ralph.sandbox.time.time", return_value=1700000000.0)
    def test_multiple_failures_collected(self, mock_time, mock_read, mock_health, mock_run):
        """All failures are collected, not just the first one."""
        mock_run.side_effect = self._run_side_effect(echo_rc=1, curl_rc=28)
        sb = DockerSandbox("/dotfiles")
        failures = sb.preflight_check(self.SANDBOX_NAME, "claude", 8080)
        # token missing + proxy down + sandbox unresponsive = 3 failures
        assert len(failures) == 3


# ---------------------------------------------------------------------------
# DockerSandbox.setup_git_config (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxSetupGitConfig:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_sets_user_name_email_and_safe_directory(self, mock_run):
        DockerSandbox("/dotfiles").setup_git_config("my-sandbox", "Ralph", "ralph@test.com")
        assert mock_run.call_count == 3

        name_call = mock_run.call_args_list[0]
        assert name_call[0][0] == [
            "docker", "sandbox", "exec", "my-sandbox",
            "git", "config", "--global", "user.name", "Ralph",
        ]

        email_call = mock_run.call_args_list[1]
        assert email_call[0][0] == [
            "docker", "sandbox", "exec", "my-sandbox",
            "git", "config", "--global", "user.email", "ralph@test.com",
        ]

        safe_call = mock_run.call_args_list[2]
        assert safe_call[0][0] == [
            "docker", "sandbox", "exec", "my-sandbox",
            "git", "config", "--global", "--add", "safe.directory", "*",
        ]


# ---------------------------------------------------------------------------
# DockerSandbox.run_iteration (mocked subprocess)
# ---------------------------------------------------------------------------

class TestSandboxRunIteration:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_writes_spec_runs_claude_reads_back(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=0, stdout="updated spec"),  # read spec
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration("my-sandbox", "original spec", "sonnet")
        assert rc == 0
        assert updated == "updated spec"

        # Verify write call pipes spec content via tee
        write_call = mock_run.call_args_list[0]
        assert write_call[1]["input"] == "original spec"
        assert "tee" in write_call[0][0]
        assert "/tmp/spec.md" in write_call[0][0]

        # Verify claude call
        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert "claude" in cmd
        assert "-w" in cmd
        assert cmd[cmd.index("-w") + 1] == "/work/tree"
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "sonnet"
        assert "--dangerously-skip-permissions" in cmd
        assert "--effort" in cmd

        # Verify read-back call
        read_call = mock_run.call_args_list[2]
        assert "cat" in read_call[0][0]
        assert "/tmp/spec.md" in read_call[0][0]

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_passes_env_vars_to_claude(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/tree"
        sb.run_iteration("my-sandbox", "spec", "sonnet",
                         env_vars={"CLAUDE_CODE_OAUTH_TOKEN": "sk-test"})

        claude_call = mock_run.call_args_list[1]
        cmd = claude_call[0][0]
        assert "-e" in cmd
        e_idx = cmd.index("-e")
        assert cmd[e_idx + 1] == "CLAUDE_CODE_OAUTH_TOKEN=sk-test"

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_returns_original_spec_on_write_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        sb = DockerSandbox("/dotfiles")
        rc, updated = sb.run_iteration("my-sandbox", "original", "sonnet")
        assert rc == 1
        assert updated == "original"
        # Only the write call should have been made
        assert mock_run.call_count == 1

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_returns_original_spec_on_read_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # run claude
            MagicMock(returncode=1, stdout=""),  # read spec fails
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration("my-sandbox", "original", "sonnet")
        assert rc == 0
        assert updated == "original"

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_returns_claude_exit_code(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=42),  # claude fails
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, _ = sb.run_iteration("my-sandbox", "spec", "sonnet")
        assert rc == 42

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_cursor_writes_secret_file_and_uses_shell_wrapper(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # write api key
            MagicMock(returncode=0),  # run cursor-agent via sh -c
            MagicMock(returncode=0, stdout="updated spec"),  # read spec
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration(
            "my-sandbox", "original spec", "auto",
            agent="cursor", api_key="test-api-key-123")
        assert rc == 0
        assert updated == "updated spec"

        # Verify secret file write
        key_call = mock_run.call_args_list[1]
        assert key_call[1]["input"] == "test-api-key-123"
        assert "tee" in key_call[0][0]
        assert "/tmp/.agent-api-key" in key_call[0][0]

        # Verify shell wrapper command
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

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_cursor_no_env_vars_in_docker_exec(self, mock_run):
        """Cursor agent should not pass env vars via docker exec -e flags."""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # write api key
            MagicMock(returncode=0),  # run cursor-agent
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/tree"
        sb.run_iteration("my-sandbox", "spec", "auto",
                         agent="cursor", api_key="key123")

        agent_call = mock_run.call_args_list[2]
        cmd = agent_call[0][0]
        assert "-e" not in cmd

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_cursor_returns_original_spec_on_key_write_failure(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=1),  # write api key fails
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/tree"
        rc, updated = sb.run_iteration(
            "my-sandbox", "original", "auto",
            agent="cursor", api_key="key123")
        assert rc == 1
        assert updated == "original"
        # Only spec write + key write calls
        assert mock_run.call_count == 2

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_cursor_workdir_set_in_exec(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0),  # write spec
            MagicMock(returncode=0),  # write api key
            MagicMock(returncode=0),  # run cursor-agent
            MagicMock(returncode=0, stdout="spec"),  # read spec
        ]
        sb = DockerSandbox("/dotfiles")
        sb._worktree_path = "/work/my-project"
        sb.run_iteration("my-sandbox", "spec", "auto",
                         agent="cursor", api_key="key")

        agent_call = mock_run.call_args_list[2]
        cmd = agent_call[0][0]
        assert "-w" in cmd
        assert cmd[cmd.index("-w") + 1] == "/work/my-project"


# ---------------------------------------------------------------------------
# DockerSandbox.sync_to_host
# ---------------------------------------------------------------------------

class TestSandboxSyncToHost:
    @patch("ralph.sandbox.docker.subprocess.run")
    def test_returns_true_when_host_can_see_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = DockerSandbox("/dotfiles").sync_to_host("sandbox", "abc123", "def456", "/work")
        assert result is True
        cmd = mock_run.call_args[0][0]
        assert "rev-parse" in cmd
        assert "--verify" in cmd
        assert "def456" in cmd

    @patch("ralph.sandbox.docker.subprocess.run")
    def test_returns_false_when_host_cannot_see_commit(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = DockerSandbox("/dotfiles").sync_to_host("sandbox", "abc", "def", "/work")
        assert result is False


# ---------------------------------------------------------------------------
# ITERATION_PROMPT content
# ---------------------------------------------------------------------------

class TestIterationPrompt:
    """Verify ITERATION_PROMPT contains required execution instructions."""

    def test_contains_blocked_marker_rule(self):
        assert "[blocked:" in DockerSandbox.ITERATION_PROMPT

    def test_contains_run_all_checks_rule(self):
        assert "Run all checks" in DockerSandbox.ITERATION_PROMPT

    def test_contains_spec_maintenance_rules(self):
        assert "Spec maintenance rules" in DockerSandbox.ITERATION_PROMPT

    def test_contains_step_structure(self):
        assert "For each task, follow this workflow" in DockerSandbox.ITERATION_PROMPT

    def test_contains_unfulfillable_tasks_section(self):
        assert "Unfulfillable tasks" in DockerSandbox.ITERATION_PROMPT
