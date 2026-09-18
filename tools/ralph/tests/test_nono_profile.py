"""Unit tests for ralph.runtime.nono_profile — nono profile builders."""

import json
import os
import pathlib
from unittest.mock import patch

import pytest

from ralph.runtime.nono_profile import (
    ALLOW_VARS,
    DEFAULT_UPSTREAM,
    LINUX_PORTRANGE_PATH,
    MACOS_PORTRANGE_FIRST,
    MACOS_PORTRANGE_LAST,
    PROFILE_VERSION,
    build_profile,
    credential_route,
    ephemeral_port_range,
    git_grants,
    normalize_auth_mode,
    write_project_profile,
)


# ---------------------------------------------------------------------------
# ephemeral_port_range
# ---------------------------------------------------------------------------

class TestEphemeralPortRange:
    def test_darwin_reads_both_sysctls(self):
        values = {
            MACOS_PORTRANGE_FIRST: "49152\n",
            MACOS_PORTRANGE_LAST: "65535\n",
        }
        seen = []

        def reader(source):
            seen.append(source)
            return values[source]

        assert ephemeral_port_range("darwin", reader) == (49152, 65535)
        assert seen == [MACOS_PORTRANGE_FIRST, MACOS_PORTRANGE_LAST]

    def test_darwin_default_range_is_exactly_at_the_limit(self):
        """The stock macOS range is 16384 ports and must be accepted."""
        reader = {MACOS_PORTRANGE_FIRST: "49152",
                  MACOS_PORTRANGE_LAST: "65535"}.__getitem__
        first, last = ephemeral_port_range("darwin", reader)
        assert last - first + 1 == 16384

    def test_darwin_range_too_wide_raises(self):
        reader = {MACOS_PORTRANGE_FIRST: "49151",
                  MACOS_PORTRANGE_LAST: "65535"}.__getitem__
        with pytest.raises(ValueError) as exc:
            ephemeral_port_range("darwin", reader)
        assert str(exc.value) == (
            "ralph: ephemeral port range exceeds nono macOS limit of 16384")

    def test_linux_parses_procfs(self):
        seen = []

        def reader(source):
            seen.append(source)
            return "32768\t60999\n"

        assert ephemeral_port_range("linux", reader) == (32768, 60999)
        assert seen == [LINUX_PORTRANGE_PATH]

    def test_linux_range_is_not_limited(self):
        """The macOS width limit does not apply to Landlock/seccomp."""
        assert ephemeral_port_range(
            "linux", lambda source: "1024 65535") == (1024, 65535)

    def test_linux_malformed_raises(self):
        with pytest.raises(ValueError) as exc:
            ephemeral_port_range("linux", lambda source: "32768\n")
        assert "malformed ephemeral port range" in str(exc.value)

    def test_darwin_non_numeric_raises(self):
        with pytest.raises(ValueError) as exc:
            ephemeral_port_range("darwin", lambda source: "nope")
        assert "malformed port number" in str(exc.value)

    def test_inverted_range_raises(self):
        with pytest.raises(ValueError) as exc:
            ephemeral_port_range("linux", lambda source: "60999 32768")
        assert "inverted" in str(exc.value)

    def test_platform_defaults_to_sys_platform_at_call_time(self, monkeypatch):
        monkeypatch.setattr("ralph.runtime.nono_profile.sys.platform", "darwin")
        reader = {MACOS_PORTRANGE_FIRST: "49152",
                  MACOS_PORTRANGE_LAST: "65535"}.__getitem__
        assert ephemeral_port_range(reader=reader) == (49152, 65535)

    def test_sysctl_failure_reported_as_value_error(self):
        with patch("ralph.runtime.nono_profile.subprocess.run") as run:
            run.side_effect = FileNotFoundError("no sysctl")
            with pytest.raises(ValueError) as exc:
                ephemeral_port_range("darwin")
        assert "could not read sysctl" in str(exc.value)

    def test_unreadable_procfs_reported_as_value_error(self, tmp_path):
        with patch("ralph.runtime.nono_profile.LINUX_PORTRANGE_PATH",
                   str(tmp_path / "missing")):
            with pytest.raises(ValueError) as exc:
                ephemeral_port_range("linux")
        assert "could not read" in str(exc.value)

    def test_default_reader_shells_out_to_sysctl(self):
        with patch("ralph.runtime.nono_profile.subprocess.run") as run:
            run.return_value.stdout = "49152\n"
            ephemeral_port_range("darwin")
        assert run.call_args_list[0][0][0] == [
            "sysctl", "-n", MACOS_PORTRANGE_FIRST]
        assert run.call_args_list[1][0][0] == [
            "sysctl", "-n", MACOS_PORTRANGE_LAST]

    def test_default_reader_reads_procfs(self, tmp_path):
        proc = tmp_path / "ip_local_port_range"
        proc.write_text("32768\t60999\n")
        with patch("ralph.runtime.nono_profile.LINUX_PORTRANGE_PATH",
                   str(proc)):
            assert ephemeral_port_range("linux") == (32768, 60999)


# ---------------------------------------------------------------------------
# credential_route / normalize_auth_mode
# ---------------------------------------------------------------------------

class TestNormalizeAuthMode:
    def test_none_is_the_agent_default(self):
        assert normalize_auth_mode(None) == "oauth"

    def test_cli_hyphen_form_normalized(self):
        assert normalize_auth_mode("api-key") == "api_key"

    def test_underscore_form_passes_through(self):
        assert normalize_auth_mode("api_key") == "api_key"

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError) as exc:
            normalize_auth_mode("saml")
        assert "unknown auth mode 'saml'" in str(exc.value)


class TestCredentialRoute:
    def test_oauth(self):
        assert credential_route("oauth", {}) == {
            "upstream": DEFAULT_UPSTREAM,
            "credential_key": "cmd://ralph_token",
            "env_var": "CLAUDE_CODE_OAUTH_TOKEN",
            "inject_header": "Authorization",
            "credential_format": "Bearer {}",
        }

    def test_api_key(self):
        assert credential_route("api_key", None) == {
            "upstream": DEFAULT_UPSTREAM,
            "credential_key": "cmd://ralph_token",
            "env_var": "ANTHROPIC_API_KEY",
            "inject_header": "x-api-key",
            "credential_format": "{}",
        }

    def test_gateway(self):
        assert credential_route("gateway", {}) == {
            "upstream": DEFAULT_UPSTREAM,
            "credential_key": "cmd://ralph_token",
            "env_var": "ANTHROPIC_AUTH_TOKEN",
            "inject_header": "Authorization",
            "credential_format": "Bearer {}",
        }

    def test_gateway_base_url_becomes_upstream(self):
        route = credential_route(
            "gateway", {"baseUrl": "https://gateway.example.com",
                        "modelPrefix": "llm-gateway"})
        assert route["upstream"] == "https://gateway.example.com"

    def test_api_key_base_url_becomes_upstream(self):
        route = credential_route("api-key",
                                 {"baseUrl": "https://api.example.com"})
        assert route["upstream"] == "https://api.example.com"

    def test_empty_base_url_falls_back_to_default(self):
        assert credential_route(
            "oauth", {"baseUrl": ""})["upstream"] == DEFAULT_UPSTREAM

    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError):
            credential_route("saml", {})


# ---------------------------------------------------------------------------
# build_profile
# ---------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path):
    """A HOME with a worktree, state dir, and a couple of PATH dirs.

    Returned fully resolved: build_profile realpaths the claude binary, so
    a symlinked temp root (``/var`` on macOS) would otherwise make the
    expected and produced read grants differ.
    """
    h = tmp_path / "home"
    for sub in ("bin", ".fnm/bin", "src/proj", ".ralph/nono/sbx", "tools"):
        (h / sub).mkdir(parents=True)
    (h / "tools" / "claude").write_text("#!/bin/sh\n")
    return pathlib.Path(os.path.realpath(h))


def make_profile(home_dir, **overrides):
    """Build a profile with sensible defaults for the fixture HOME.

    The parameter is ``home_dir``, not ``home``, so tests can override the
    profile's own ``home`` argument by keyword.
    """
    home = home_dir
    kwargs = {
        "sandbox_name": "agent-loop-claude-feature-x",
        "worktree": str(home / "src/proj"),
        "git_common_dir": str(home / "src/proj/.git"),
        "git_dir": str(home / "src/proj/.git/worktrees/sbx"),
        "state_dir": str(home / "src/proj/.git/ralph/nono/sbx"),
        "home": str(home),
        "path_env": os.pathsep.join(
            ["/usr/bin", str(home / "bin"), str(home / ".fnm/bin")]),
        "claude_bin": str(home / "tools" / "claude"),
        "allowed_hosts": ["api.anthropic.com", "sentry.io"],
        "network": "filtered",
        "auth_mode": "oauth",
        "token_data": {},
        "dotfiles_dir": "/dotfiles",
        "spec_dir": None,
        "port_range": (49152, 65535),
        "project_profile_name": None,
    }
    kwargs.update(overrides)
    return build_profile(**kwargs)


def path_reads(profile, home):
    """Read grants other than the repo's git dir, which every profile has."""
    return [path for path in profile["filesystem"]["read"]
            if path != str(home / "src/proj/.git")]


class TestBuildProfileShape:
    def test_filtered_profile(self, home):
        profile = make_profile(
            home, spec_dir=str(home / "src/proj/.git/ralph/nono/sbx/spec-ab12"))
        assert profile == {
            "meta": {"name": "ralph-agent-loop-claude-feature-x",
                     "version": PROFILE_VERSION},
            "filesystem": {
                "allow": [
                    str(home / "src/proj"),
                    str(home / "src/proj/.git/objects"),
                    str(home / "src/proj/.git/refs"),
                    str(home / "src/proj/.git/logs"),
                    str(home / "src/proj/.git/worktrees/sbx"),
                    str(home / "src/proj/.git/ralph/nono/sbx/spec-ab12"),
                    str(home / ".ralph/claude-config"),
                    str(home / ".ralph/claude-config.lock"),
                ],
                "read": [
                    str(home / "src/proj/.git"),
                    str(home / "bin"),
                    str(home / ".fnm/bin"),
                    str(home / "tools"),
                ],
                "read_file": [
                    str(home / "src/proj/.git/ralph/nono/sbx/gitconfig")],
            },
            "environment": {"allow_vars": ALLOW_VARS},
            "network": {
                "allow_domain": ["api.anthropic.com", "sentry.io"],
                "open_port_range": [[49152, 65535]],
                "credentials": ["anthropic"],
                "custom_credentials": {
                    "anthropic": credential_route("oauth", {}),
                },
            },
            "credential_capture": {
                "ralph_token": {
                    "command": ["/dotfiles/scripts/ralph", "get-token",
                                "--agent", "claude", "--auth", "oauth"],
                    "timeout_secs": 10,
                    "cache_ttl_secs": 300,
                },
            },
        }

    def test_unrestricted_omits_network_filters(self, home):
        profile = make_profile(home, network="unrestricted")
        assert profile["network"] == {
            "credentials": ["anthropic"],
            "custom_credentials": {"anthropic": credential_route("oauth", {})},
        }

    def test_unrestricted_needs_no_port_range(self, home):
        profile = make_profile(home, network="unrestricted", port_range=None)
        assert "open_port_range" not in profile["network"]

    def test_filtered_without_port_range_raises(self, home):
        with pytest.raises(ValueError) as exc:
            make_profile(home, port_range=None)
        assert "filtered network requires an ephemeral port range" in str(exc.value)

    def test_unknown_network_mode_raises(self, home):
        with pytest.raises(ValueError) as exc:
            make_profile(home, network="open")
        assert "unknown network mode 'open'" in str(exc.value)

    def test_no_spec_dir_grants_no_spec_path(self, home):
        """Nothing under the state dir is writable without a spec dir."""
        allow = make_profile(home)["filesystem"]["allow"]
        assert not any("/ralph/nono/" in path for path in allow)

    def test_spec_dir_is_granted_as_a_directory(self, home):
        """A file grant would not survive an atomic save (temp + rename)."""
        spec_dir = str(home / "src/proj/.git/ralph/nono/sbx/spec-ab12")
        profile = make_profile(home, spec_dir=spec_dir)
        assert spec_dir in profile["filesystem"]["allow"]
        assert "allow_file" not in profile["filesystem"]

    def test_allow_vars_matches_the_spec_list(self, home):
        """Pinned literally: nono drops every variable not listed here.

        Losing DOCKER_HOST silently breaks the Docker socket proxy, losing
        GIT_CONFIG_GLOBAL silently drops the generated git identity.
        """
        assert make_profile(home)["environment"]["allow_vars"] == [
            "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG",
            "LC_*", "TMPDIR", "JAVA_HOME",
            "CLAUDE_CONFIG_DIR", "GIT_CONFIG_GLOBAL", "DOCKER_HOST",
            "ANTHROPIC_CUSTOM_MODEL_OPTION", "ANTHROPIC_DEFAULT_*_MODEL",
            "CLAUDE_CODE_SUBAGENT_MODEL",
        ]

    def test_allow_vars_is_a_copy(self, home):
        """Mutating a built profile must not corrupt the module constant."""
        profile = make_profile(home)
        profile["environment"]["allow_vars"].append("SECRET")
        assert "SECRET" not in ALLOW_VARS

    def test_extends_project_profile(self, home):
        profile = make_profile(home, project_profile_name="project")
        assert profile["extends"] == "project"

    def test_no_extends_without_project_profile(self, home):
        assert "extends" not in make_profile(home)

    def test_no_grant_reaches_the_users_own_config(self, home):
        """No grant may name — or contain — the user's real config.

        The agent gets a dedicated config dir under ~/.ralph; the user's
        Claude state, git identity, and keys stay outside the sandbox.
        Granting HOME itself, or ~/.claude, would hand all three over.
        """
        profile = make_profile(
            home, spec_dir=str(home / "src/proj/.git/ralph/nono/sbx/spec-ab12"))
        granted = sum((profile["filesystem"][key] for key in
                       ("allow", "read", "read_file")), [])

        for secret in (home, home / ".claude", home / ".claude.json",
                       home / ".gitconfig", home / ".ssh"):
            for path in granted:
                assert path != str(secret), path
                assert not str(secret).startswith(path + "/"), path

    def test_local_claude_install_grants_only_its_own_directory(self, home):
        """A ~/.claude/local install is read-granted at local/, not ~/.claude.

        That layout puts the CLI inside the config dir; the grant must not
        widen to the credentials and session state beside it.
        """
        local = home / ".claude" / "local"
        local.mkdir(parents=True)
        (local / "claude").write_text("#!/bin/sh\n")
        profile = make_profile(home, claude_bin=str(local / "claude"),
                               path_env=str(local))
        assert path_reads(profile, home) == [str(local)]


class TestBuildProfileFilesystem:
    def test_path_entries_outside_home_are_dropped(self, home):
        profile = make_profile(
            home, path_env=os.pathsep.join(
                ["/usr/bin", "/opt/homebrew/bin", str(home / "bin")]))
        assert path_reads(profile, home) == [
            str(home / "bin"), str(home / "tools")]

    def test_nonexistent_path_entries_are_dropped(self, home):
        profile = make_profile(
            home, path_env=os.pathsep.join(
                [str(home / "bin"), str(home / "gone")]))
        assert str(home / "gone") not in profile["filesystem"]["read"]

    def test_empty_path_entries_are_dropped(self, home):
        profile = make_profile(home, path_env=f"{home / 'bin'}{os.pathsep}")
        assert path_reads(profile, home) == [
            str(home / "bin"), str(home / "tools")]

    def test_duplicate_path_entries_deduped(self, home):
        path_env = os.pathsep.join([str(home / "bin")] * 3)
        profile = make_profile(home, path_env=path_env)
        assert path_reads(profile, home) == [
            str(home / "bin"), str(home / "tools")]

    def test_path_entries_normalized_before_dedupe(self, home):
        """'~/bin/' and '~/bin/.' are one grant, not three."""
        path_env = os.pathsep.join([
            str(home / "bin") + "/", str(home / "bin") + "/.",
            str(home / "bin")])
        profile = make_profile(home, path_env=path_env)
        assert path_reads(profile, home) == [
            str(home / "bin"), str(home / "tools")]

    def test_claude_bin_symlink_is_resolved(self, home):
        link = home / "bin" / "claude"
        link.symlink_to(home / "tools" / "claude")
        profile = make_profile(home, claude_bin=str(link))
        assert str(home / "tools") in profile["filesystem"]["read"]

    def test_claude_bin_dir_not_duplicated_when_already_on_path(self, home):
        profile = make_profile(
            home, claude_bin=str(home / "bin" / "claude"),
            path_env=str(home / "bin"))
        assert path_reads(profile, home) == [str(home / "bin")]

    @pytest.mark.parametrize("arg", [
        "worktree", "git_common_dir", "git_dir", "state_dir", "home",
        "claude_bin"])
    def test_missing_path_argument_raises(self, home, arg):
        """A dropped grant would surface as a permission error mid-run."""
        with pytest.raises(ValueError) as exc:
            make_profile(home, **{arg: None})
        assert f"nono profile requires {arg}" in str(exc.value)

    @pytest.mark.parametrize("arg", [
        "worktree", "git_common_dir", "git_dir", "state_dir", "home",
        "claude_bin", "spec_dir"])
    def test_relative_path_argument_raises(self, home, arg):
        """nono resolves grants itself, so a relative path grants elsewhere."""
        with pytest.raises(ValueError) as exc:
            make_profile(home, **{arg: "src/proj"})
        assert f"nono profile {arg} must be an absolute path" in str(exc.value)

    def test_empty_claude_bin_does_not_grant_the_cwd(self, home):
        """dirname(realpath('')) is the cwd's parent — never a grant."""
        with pytest.raises(ValueError):
            make_profile(home, claude_bin="")


class TestBuildProfileNetwork:
    def test_hosts_deduped_lowercased_and_sorted(self, home):
        profile = make_profile(home, allowed_hosts=[
            "sentry.io", "API.anthropic.com", "api.anthropic.com",
            "  registry.npmjs.org  ", "sentry.io"])
        assert profile["network"]["allow_domain"] == [
            "api.anthropic.com", "registry.npmjs.org", "sentry.io"]

    def test_empty_hosts_yield_empty_allowlist(self, home):
        assert make_profile(home, allowed_hosts=[])["network"]["allow_domain"] == []
        assert make_profile(home, allowed_hosts=None)["network"]["allow_domain"] == []

    def test_blank_hosts_dropped(self, home):
        profile = make_profile(home, allowed_hosts=["", "  ", "sentry.io"])
        assert profile["network"]["allow_domain"] == ["sentry.io"]


class TestBuildProfileCredentials:
    @pytest.mark.parametrize("auth_mode,cli_mode,env_var", [
        ("oauth", "oauth", "CLAUDE_CODE_OAUTH_TOKEN"),
        ("api_key", "api-key", "ANTHROPIC_API_KEY"),
        ("api-key", "api-key", "ANTHROPIC_API_KEY"),
        ("gateway", "gateway", "ANTHROPIC_AUTH_TOKEN"),
        (None, "oauth", "CLAUDE_CODE_OAUTH_TOKEN"),
    ])
    def test_auth_modes(self, home, auth_mode, cli_mode, env_var):
        profile = make_profile(home, auth_mode=auth_mode)
        route = profile["network"]["custom_credentials"]["anthropic"]
        assert route["env_var"] == env_var
        capture = profile["credential_capture"]["ralph_token"]
        assert capture["command"][-2:] == ["--auth", cli_mode]

    def test_unknown_auth_mode_raises(self, home):
        with pytest.raises(ValueError):
            make_profile(home, auth_mode="saml")

    def test_gateway_upstream_from_token_data(self, home):
        profile = make_profile(
            home, auth_mode="gateway",
            token_data={"baseUrl": "https://gateway.example.com",
                        "modelPrefix": "llm-gateway"})
        route = profile["network"]["custom_credentials"]["anthropic"]
        assert route["upstream"] == "https://gateway.example.com"

    def test_capture_command_is_absolute(self, home, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        profile = make_profile(home, dotfiles_dir="dotfiles")
        command = profile["credential_capture"]["ralph_token"]["command"]
        assert command[0] == str(tmp_path / "dotfiles" / "scripts" / "ralph")
        assert os.path.isabs(command[0])

    def test_profile_is_json_serializable(self, home):
        """nono reads the profile as JSON, so every value must survive it."""
        profile = make_profile(home)
        assert json.loads(json.dumps(profile)) == profile

    def test_no_real_token_in_profile(self, home):
        """The profile names a capture command; it never holds a secret."""
        blob = json.dumps(make_profile(
            home, token_data={"accessToken": "sk-ant-oat01-supersecret"}))
        assert "supersecret" not in blob


# ---------------------------------------------------------------------------
# git_grants
# ---------------------------------------------------------------------------

class TestGitGrants:
    """The git common dir root stays read-only.

    git runs hooks and honours config such as core.fsmonitor on the host,
    outside the sandbox, so an agent that could write .git/hooks or
    .git/config would be running code the sandbox never sees.
    """

    COMMON = "/repo/.git"
    WORKTREE_GIT_DIR = "/repo/.git/worktrees/feature-x"

    def test_writable_paths_are_the_commit_targets(self):
        writable, _ = git_grants(self.COMMON, self.WORKTREE_GIT_DIR)
        assert writable == [
            "/repo/.git/objects",
            "/repo/.git/refs",
            "/repo/.git/logs",
            self.WORKTREE_GIT_DIR,
        ]

    def test_common_dir_root_is_read_only(self):
        writable, readable = git_grants(self.COMMON, self.WORKTREE_GIT_DIR)
        assert readable == [self.COMMON]
        assert self.COMMON not in writable

    def test_hooks_and_config_are_not_writable(self):
        writable, _ = git_grants(self.COMMON, self.WORKTREE_GIT_DIR)
        for path in ("/repo/.git/hooks", "/repo/.git/config",
                     "/repo/.git/packed-refs"):
            for granted in writable:
                assert path != granted
                assert not path.startswith(granted + "/")

    def test_plain_checkout_grants_the_common_dir(self, tmp_path):
        """HEAD and the index live in the root there, so it must be writable."""
        common = str(tmp_path / ".git")
        writable, readable = git_grants(common, common)
        assert common in writable
        assert readable == [common]

    def test_plain_checkout_detected_through_symlinks(self, tmp_path):
        """macOS /var -> /private/var would otherwise look like a worktree."""
        real = tmp_path / "repo.git"
        real.mkdir()
        link = tmp_path / "link.git"
        link.symlink_to(real)
        writable, _ = git_grants(str(real), str(link))
        assert str(real) in writable
        assert str(link) not in writable

    def test_profile_grants_match(self, home):
        profile = make_profile(home)
        writable, readable = git_grants(
            str(home / "src/proj/.git"),
            str(home / "src/proj/.git/worktrees/sbx"))
        assert set(writable) <= set(profile["filesystem"]["allow"])
        assert set(readable) <= set(profile["filesystem"]["read"])


# ---------------------------------------------------------------------------
# write_project_profile
# ---------------------------------------------------------------------------

PROJECT_PROFILE = {
    "meta": {"name": "whatever", "version": "1.0.0"},
    "filesystem": {"allow": ["~/.gradle"], "read": ["~/.jenv"]},
    "environment": {"allow_vars": ["JAVA_HOME"]},
}


def write_project_source(project_dir, profile):
    """Write a project's own .agent-loop/nono-profile.json."""
    agent_loop = project_dir / ".agent-loop"
    agent_loop.mkdir(parents=True, exist_ok=True)
    path = agent_loop / "nono-profile.json"
    path.write_text(profile if isinstance(profile, str)
                    else json.dumps(profile))
    return path


class TestWriteProjectProfile:
    def dest(self, tmp_path):
        return str(tmp_path / "state" / "sbx" / "project.json")

    def test_returns_none_without_a_project_profile(self, tmp_path):
        dest = self.dest(tmp_path)
        assert write_project_profile(str(tmp_path), dest) is None
        assert not os.path.exists(dest)

    def test_writes_the_copy_beside_the_generated_profile(self, tmp_path):
        project = tmp_path / "proj"
        write_project_source(project, PROJECT_PROFILE)
        dest = self.dest(tmp_path)

        name = write_project_profile(str(project), dest)

        assert name == "project"
        copied = json.loads(pathlib.Path(dest).read_text())
        assert copied["filesystem"] == PROJECT_PROFILE["filesystem"]
        assert copied["environment"] == PROJECT_PROFILE["environment"]

    def test_meta_name_matches_the_file_name(self, tmp_path):
        """nono resolves 'extends' by name, so the two must agree."""
        project = tmp_path / "proj"
        write_project_source(project, PROJECT_PROFILE)
        dest = self.dest(tmp_path)

        name = write_project_profile(str(project), dest)

        copied = json.loads(pathlib.Path(dest).read_text())
        assert copied["meta"]["name"] == name == "project"
        assert copied["meta"]["version"] == "1.0.0"

    def test_meta_added_when_absent(self, tmp_path):
        project = tmp_path / "proj"
        write_project_source(project, {"filesystem": {"read": ["~/.jenv"]}})
        dest = self.dest(tmp_path)

        write_project_profile(str(project), dest)

        copied = json.loads(pathlib.Path(dest).read_text())
        assert copied["meta"]["name"] == "project"

    def test_rewrites_an_existing_copy(self, tmp_path):
        """A changed project profile must not leave the old copy behind."""
        project = tmp_path / "proj"
        write_project_source(project, PROJECT_PROFILE)
        dest = self.dest(tmp_path)
        write_project_profile(str(project), dest)

        write_project_source(project, dict(PROJECT_PROFILE,
                                           environment={"allow_vars": ["GOPATH"]}))
        write_project_profile(str(project), dest)

        copied = json.loads(pathlib.Path(dest).read_text())
        assert copied["environment"] == {"allow_vars": ["GOPATH"]}

    def test_invalid_json_raises(self, tmp_path):
        project = tmp_path / "proj"
        write_project_source(project, "{not json")
        with pytest.raises(ValueError) as exc:
            write_project_profile(str(project), self.dest(tmp_path))
        assert "is not valid JSON" in str(exc.value)

    def test_non_object_json_raises(self, tmp_path):
        project = tmp_path / "proj"
        write_project_source(project, "[1, 2, 3]")
        with pytest.raises(ValueError) as exc:
            write_project_profile(str(project), self.dest(tmp_path))
        assert "must contain a JSON object" in str(exc.value)

    def test_name_matches_generated_profile_extends(self, tmp_path, home):
        """The written name is what build_profile puts in 'extends'."""
        project = tmp_path / "proj"
        write_project_source(project, PROJECT_PROFILE)
        name = write_project_profile(str(project), self.dest(tmp_path))
        profile = make_profile(home, project_profile_name=name)
        assert profile["extends"] == name
