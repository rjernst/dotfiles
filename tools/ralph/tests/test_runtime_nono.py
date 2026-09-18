"""Unit tests for ralph.runtime.nono — NonoRuntime backend.

Every nono interaction is mocked: the implementation environment has no
nono binary.  Behaviour that depends on a real nono is covered by
``ralph selftest --runtime nono``.
"""

import glob
import json
import os
import time
from unittest.mock import MagicMock, patch

import pytest

from ralph.runtime import Runtime
from ralph.runtime.nono import MIN_NONO_VERSION, NonoRuntime
from ralph.token import keychain_service_name, keystore_read_command


DOTFILES = "/dotfiles"
BRANCH = "feature/nono"
SANDBOX = "agent-loop-claude-feature-nono"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A private HOME so state dirs never touch the real one."""
    path = tmp_path / "home"
    path.mkdir()
    monkeypatch.setenv("HOME", str(path))
    return path


@pytest.fixture
def worktree(tmp_path):
    path = tmp_path / "work" / "repo"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def git_common_dir(tmp_path):
    path = tmp_path / "work" / "repo.git"
    (path / "objects").mkdir(parents=True)
    (path / "refs").mkdir()
    return path


@pytest.fixture
def git_dir(git_common_dir):
    """The worktree's own git dir, <common>/worktrees/<name>."""
    path = git_common_dir / "worktrees" / "repo"
    path.mkdir(parents=True)
    return path


@pytest.fixture
def runtime(home, git_common_dir, git_dir):
    """A runtime anchored to the fixture repo.

    Sandbox state lives in the repo's git common dir, so every test needs
    one; patching the resolvers keeps git itself out of the unit tests.
    """
    with patch.object(NonoRuntime, "_resolve_git_common_dir",
                      return_value=str(git_common_dir)), \
         patch.object(NonoRuntime, "_resolve_git_dir",
                      return_value=str(git_dir)):
        yield NonoRuntime(DOTFILES)


def prepared(runtime, worktree, name=SANDBOX):
    """Record a sandbox's state dir and worktree without touching nono."""
    runtime._make_private_dir(runtime.state_dir(name))
    with open(runtime.worktree_file(name), "w") as f:
        f.write(str(worktree) + "\n")
    runtime._worktree_path = str(worktree)
    return name


# ---------------------------------------------------------------------------
# Construction and identity
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_defaults(self, runtime, home):
        assert runtime.network == "filtered"
        assert runtime.allowed_hosts == ()
        assert runtime.uses_credential_proxy is False
        assert runtime.proxy_listen_addr() == "127.0.0.1"
        assert runtime.proxy_host() == "127.0.0.1"
        assert runtime.home == str(home)

    def test_unknown_network_rejected(self, home):
        with pytest.raises(ValueError, match="unknown network mode"):
            NonoRuntime(DOTFILES, network="open")

    def test_state_lives_in_the_git_common_dir(self, runtime, git_common_dir):
        """State dies with the repo and never shows up in git status."""
        assert runtime.state_root() == str(git_common_dir / "ralph" / "nono")
        assert runtime.state_dir(SANDBOX) == str(
            git_common_dir / "ralph" / "nono" / SANDBOX)
        assert runtime.project_profile_path(SANDBOX).endswith(
            f"{SANDBOX}/project.json")

    def test_per_sandbox_files_sit_in_the_state_dir(self, runtime):
        assert runtime.profile_path(SANDBOX).endswith(
            f"{SANDBOX}/profile.json")
        assert runtime.gitconfig_path(SANDBOX).endswith(f"{SANDBOX}/gitconfig")

    def test_shared_paths_live_under_home(self, runtime, home):
        """Both are shared by every project, so they stay in ~/.ralph."""
        assert runtime.claude_config_dir() == str(
            home / ".ralph" / "claude-config")
        assert runtime.docker_socket_path() == str(
            home / ".ralph" / "docker-proxy.sock")

    def test_without_a_repository_state_has_no_home(self, home, tmp_path,
                                                    monkeypatch):
        monkeypatch.chdir(tmp_path)
        rt = NonoRuntime(DOTFILES)
        with patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=None):
            with pytest.raises(RuntimeError, match="is not a git repository"):
                rt.state_root()

    def test_sandbox_name_matches_base_class(self, runtime):
        assert runtime.sandbox_name("claude", BRANCH) == SANDBOX
        # No socket-path length cap for this backend.
        assert NonoRuntime._max_sandbox_name_length() is None


# ---------------------------------------------------------------------------
# check_prerequisites
# ---------------------------------------------------------------------------

class TestCheckPrerequisites:
    @staticmethod
    def _which(missing=()):
        return lambda name: None if name in missing else f"/usr/bin/{name}"

    def _run(self, runtime, missing=(), version_out="nono 0.77.0\n", rc=0,
             error=None):
        result = MagicMock(returncode=rc, stdout=version_out, stderr="")
        with patch("ralph.runtime.nono.shutil.which",
                   side_effect=self._which(missing)), \
             patch("ralph.runtime.nono.subprocess.run",
                   side_effect=error or None,
                   return_value=result):
            return runtime.check_prerequisites()

    def test_all_present(self, runtime):
        assert self._run(runtime) == []

    def test_newer_version_accepted(self, runtime):
        assert self._run(runtime, version_out="nono 1.4.0") == []

    def test_nono_missing(self, runtime):
        errors = self._run(runtime, missing={"nono"})
        assert errors == ["nono is not installed (install: see https://nono.sh/docs)"]

    def test_nono_too_old(self, runtime):
        errors = self._run(runtime, version_out="nono 0.76.9")
        assert errors == ["nono 0.76.9 is too old — 0.77.0 or newer is required"]

    def test_version_unparseable(self, runtime):
        errors = self._run(runtime, version_out="nono (dev build)")
        assert errors == [
            "could not determine nono version — 0.77.0 or newer is required"]

    def test_version_command_fails(self, runtime):
        errors = self._run(runtime, rc=1)
        assert len(errors) == 1
        assert "could not determine nono version" in errors[0]

    def test_version_command_missing_binary(self, runtime):
        errors = self._run(runtime, error=OSError("boom"))
        assert len(errors) == 1
        assert "could not determine nono version" in errors[0]

    def test_claude_missing(self, runtime):
        assert self._run(runtime, missing={"claude"}) == ["claude is not installed"]

    def test_docker_missing(self, runtime):
        assert self._run(runtime, missing={"docker"}) == [
            "docker is not installed (required for the docker socket proxy)"]

    def test_all_missing_reports_each(self, runtime):
        errors = self._run(runtime, missing={"nono", "claude", "docker"})
        assert len(errors) == 3

    def test_version_falls_back_to_stderr(self, runtime):
        result = MagicMock(returncode=0, stdout="", stderr="nono 0.80.1\n")
        with patch("ralph.runtime.nono.shutil.which",
                   side_effect=self._which()), \
             patch("ralph.runtime.nono.subprocess.run", return_value=result):
            assert runtime.check_prerequisites() == []

    def test_parse_version(self):
        assert NonoRuntime._parse_version("nono 0.77.0") == (0, 77, 0)
        assert NonoRuntime._parse_version("v1.2.30-beta.1") == (1, 2, 30)
        assert NonoRuntime._parse_version("no digits here") is None
        assert NonoRuntime._parse_version("") is None
        assert NonoRuntime._parse_version(None) is None

    def test_min_version_is_0_77_0(self):
        assert MIN_NONO_VERSION == (0, 77, 0)


# ---------------------------------------------------------------------------
# Images (no-ops)
# ---------------------------------------------------------------------------

class TestImages:
    def test_ensure_image_returns_host(self, runtime):
        assert runtime.ensure_image("claude") == "host"

    def test_ensure_project_image_returns_host(self, runtime, tmp_path):
        assert runtime.ensure_project_image(
            "claude", "host", str(tmp_path)) == "host"

    def test_warns_once_per_ignored_file(self, home, tmp_path, capsys):
        project = tmp_path / "project"
        (project / ".agent-loop").mkdir(parents=True)
        (project / ".agent-loop" / "dependencies").write_text("jq\n")
        (project / ".agent-loop" / "Dockerfile.sandbox").write_text("FROM x\n")

        rt = NonoRuntime(DOTFILES, project_dir=str(project))
        rt.ensure_image("claude")
        out = capsys.readouterr().err
        assert f"ralph: warning: {project}/.agent-loop/Dockerfile.sandbox " \
               "is ignored by the nono runtime" in out
        assert f"ralph: warning: {project}/.agent-loop/dependencies " \
               "is ignored by the nono runtime" in out

        rt.ensure_image("claude")
        assert capsys.readouterr().err == ""

    def test_no_warning_without_project_files(self, runtime, tmp_path, capsys):
        runtime.ensure_project_image("claude", "host", str(tmp_path))
        assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# ensure_sandbox
# ---------------------------------------------------------------------------

class TestEnsureSandbox:
    def _ensure(self, runtime, worktree, project_dir=None):
        # The timestamp file lives outside the state dir, under the real
        # ~/.ralph — keep it mocked so tests leave no trace.
        with patch("ralph.runtime.nono.ensure_docker_proxy_socket") as sock, \
             patch.object(NonoRuntime, "_touch_sandbox_timestamp") as touch:
            name = runtime.ensure_sandbox("claude", BRANCH, str(worktree),
                                          project_dir=project_dir)
        self.touch = touch
        return name, sock

    def test_creates_private_state_dir(self, runtime, worktree):
        name, _ = self._ensure(runtime, worktree)
        assert name == SANDBOX
        state_dir = runtime.state_dir(name)
        assert os.path.isdir(state_dir)
        assert oct(os.stat(state_dir).st_mode & 0o777) == oct(0o700)

    def test_records_absolute_worktree(self, runtime, worktree):
        name, _ = self._ensure(runtime, worktree)
        with open(runtime.worktree_file(name)) as f:
            assert f.read().strip() == str(worktree)
        assert runtime._read_worktree(name) == str(worktree)

    def test_creates_private_claude_config_dir(self, runtime, worktree):
        self._ensure(runtime, worktree)
        config_dir = runtime.claude_config_dir()
        assert os.path.isdir(config_dir)
        assert oct(os.stat(config_dir).st_mode & 0o777) == oct(0o700)

    def test_starts_docker_socket_proxy(self, runtime, worktree):
        _, sock = self._ensure(runtime, worktree)
        sock.assert_called_once_with(
            DOTFILES, socket_path=runtime.docker_socket_path())

    def test_touches_timestamp(self, runtime, worktree):
        name, _ = self._ensure(runtime, worktree)
        self.touch.assert_called_once_with(name)

    def test_never_touches_real_claude_dir(self, runtime, worktree, home):
        self._ensure(runtime, worktree)
        assert not (home / ".claude").exists()
        assert not (home / ".gitconfig").exists()

    def test_copies_project_profile_beside_the_generated_one(
            self, runtime, worktree, tmp_path):
        """nono resolves 'extends' there, so nothing is installed globally."""
        project = tmp_path / "project"
        (project / ".agent-loop").mkdir(parents=True)
        (project / ".agent-loop" / "nono-profile.json").write_text(
            json.dumps({"filesystem": {"allow": ["/opt/tools"]}}))

        name, _ = self._ensure(runtime, worktree, project_dir=str(project))
        assert runtime._project_profile_name == "project"
        path = runtime.project_profile_path(name)
        assert os.path.dirname(path) == runtime.state_dir(name)
        with open(path) as f:
            profile = json.load(f)
        assert profile["meta"]["name"] == "project"
        assert profile["filesystem"]["allow"] == ["/opt/tools"]

    def test_project_profile_goes_with_the_sandbox(self, runtime, worktree,
                                                  tmp_path):
        """No global copy means no cleanup, and no race with another loop."""
        project = tmp_path / "project"
        (project / ".agent-loop").mkdir(parents=True)
        (project / ".agent-loop" / "nono-profile.json").write_text("{}")

        name, _ = self._ensure(runtime, worktree, project_dir=str(project))
        with patch.object(NonoRuntime, "_remove_sandbox_timestamp"):
            runtime.remove_sandbox(name)
        assert not os.path.exists(runtime.project_profile_path(name))

    def test_no_project_profile_when_absent(self, runtime, worktree, tmp_path):
        self._ensure(runtime, worktree, project_dir=str(tmp_path))
        assert runtime._project_profile_name is None

    def test_falls_back_to_constructor_project_dir(self, home, worktree,
                                                   git_common_dir, tmp_path,
                                                   capsys):
        project = tmp_path / "project"
        (project / ".agent-loop").mkdir(parents=True)
        (project / ".agent-loop" / "dependencies").write_text("jq\n")
        rt = NonoRuntime(DOTFILES, project_dir=str(project))
        with patch("ralph.runtime.nono.ensure_docker_proxy_socket"), \
             patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=str(git_common_dir)):
            rt.ensure_sandbox("claude", BRANCH, str(worktree))
        assert "is ignored by the nono runtime" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# setup_git_config
# ---------------------------------------------------------------------------

class TestSetupGitConfig:
    def test_writes_gitconfig(self, runtime, worktree):
        prepared(runtime, worktree)
        runtime.setup_git_config(SANDBOX, "Ada Lovelace", "ada@example.com")
        with open(runtime.gitconfig_path(SANDBOX)) as f:
            content = f.read()
        assert content == (
            "[user]\n"
            "\tname = Ada Lovelace\n"
            "\temail = ada@example.com\n"
            "[commit]\n"
            "\tgpgsign = false\n"
            "[tag]\n"
            "\tgpgsign = false\n"
            "[safe]\n"
            "\tdirectory = *\n"
            "[gc]\n"
            "\tauto = 0\n"
            "[maintenance]\n"
            "\tauto = false\n"
        )

    def test_signing_is_off(self, runtime, worktree):
        """The sandbox has no signing key, so a signed commit would fail."""
        prepared(runtime, worktree)
        runtime.setup_git_config(SANDBOX, "Ada", "ada@example.com")
        with open(runtime.gitconfig_path(SANDBOX)) as f:
            content = f.read()
        assert "gpgsign = false" in content
        assert "signingkey" not in content

    def test_auto_gc_is_off(self, runtime, worktree):
        """Repacking writes the git common dir root, which is read-only."""
        prepared(runtime, worktree)
        runtime.setup_git_config(SANDBOX, "Ada", "ada@example.com")
        with open(runtime.gitconfig_path(SANDBOX)) as f:
            content = f.read()
        assert "[gc]\n\tauto = 0\n" in content
        assert "[maintenance]\n\tauto = false\n" in content

    def test_never_runs_git_config_global(self, runtime, worktree):
        prepared(runtime, worktree)
        with patch("ralph.runtime.nono.subprocess.run") as run:
            runtime.setup_git_config(SANDBOX, "Ada", "ada@example.com")
        run.assert_not_called()

    def test_creates_state_dir_if_missing(self, runtime, home):
        runtime.setup_git_config(SANDBOX, "Ada", "ada@example.com")
        assert os.path.isfile(runtime.gitconfig_path(SANDBOX))

    def test_does_not_write_user_gitconfig(self, runtime, worktree, home):
        prepared(runtime, worktree)
        runtime.setup_git_config(SANDBOX, "Ada", "ada@example.com")
        assert not (home / ".gitconfig").exists()


# ---------------------------------------------------------------------------
# run_iteration
# ---------------------------------------------------------------------------

PORT_RANGE = (49152, 65535)
CLAUDE_BIN = "/usr/local/bin/claude"


def run_iteration(runtime, worktree, git_common_dir, spec="# spec\n",
                  model="sonnet", env_vars=None, rc=0, updated=None,
                  agent="claude"):
    """Run one iteration with nono mocked out. Returns (result, capture)."""
    capture = {}

    def fake_run(cmd, **kwargs):
        capture["cmd"] = list(cmd)
        capture["env"] = kwargs.get("env")
        capture["cwd"] = kwargs.get("cwd")
        spec_dirs = glob.glob(os.path.join(runtime.state_dir(SANDBOX),
                                           "spec-*"))
        capture["spec_dirs_during"] = spec_dirs
        specs = [os.path.join(d, "spec.md") for d in spec_dirs]
        capture["specs_during"] = specs
        with open(specs[0]) as f:
            capture["spec_content"] = f.read()
        with open(runtime.profile_path(SANDBOX)) as f:
            capture["profile"] = json.load(f)
        if updated is not None:
            with open(specs[0], "w") as f:
                f.write(updated)
        return MagicMock(returncode=rc)

    with patch("ralph.runtime.nono.subprocess.run", side_effect=fake_run), \
         patch("ralph.runtime.nono.shutil.which", return_value=CLAUDE_BIN), \
         patch("ralph.runtime.nono.ephemeral_port_range",
               return_value=PORT_RANGE), \
         patch.object(NonoRuntime, "_resolve_git_common_dir",
                      return_value=str(git_common_dir)):
        result = runtime.run_iteration(SANDBOX, spec, model,
                                       env_vars=env_vars, agent=agent)
    return result, capture


class TestRunIteration:
    def test_filtered_command(self, runtime, worktree, git_common_dir):
        prepared(runtime, worktree)
        (rc, _), cap = run_iteration(runtime, worktree, git_common_dir)
        assert rc == 0
        spec_path = cap["specs_during"][0]
        assert cap["cmd"] == [
            "nono", "run",
            "-p", runtime.profile_path(SANDBOX),
            "--workdir", str(worktree),
            "--no-rollback",
            "--suppress-save-prompt",
            "--allow-unix-socket", runtime.docker_socket_path(),
            "--",
            "claude",
            "-p", Runtime.iteration_prompt(spec_path),
            "--model", "sonnet",
            "--dangerously-skip-permissions",
            "--effort", "high",
        ]
        assert cap["cwd"] == str(worktree)

    def test_unrestricted_adds_allow_net(self, home, worktree, git_common_dir):
        rt = NonoRuntime(DOTFILES, network="unrestricted")
        prepared(rt, worktree)
        with patch("ralph.runtime.nono.subprocess.run",
                   return_value=MagicMock(returncode=0)) as run, \
             patch("ralph.runtime.nono.shutil.which", return_value=CLAUDE_BIN), \
             patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=str(git_common_dir)):
            rt.run_iteration(SANDBOX, "# spec\n", "sonnet")
        cmd = run.call_args[0][0]
        assert cmd[cmd.index("--allow-unix-socket") + 2] == "--allow-net"
        assert cmd[cmd.index("--allow-net") + 1] == "--"

    def test_unrestricted_reads_no_port_range(self, home, worktree,
                                              git_common_dir):
        rt = NonoRuntime(DOTFILES, network="unrestricted")
        prepared(rt, worktree)
        with patch("ralph.runtime.nono.subprocess.run",
                   return_value=MagicMock(returncode=0)), \
             patch("ralph.runtime.nono.shutil.which", return_value=CLAUDE_BIN), \
             patch("ralph.runtime.nono.ephemeral_port_range") as ports, \
             patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=str(git_common_dir)):
            rt.run_iteration(SANDBOX, "# spec\n", "sonnet")
        ports.assert_not_called()
        with open(rt.profile_path(SANDBOX)) as f:
            profile = json.load(f)
        assert "allow_domain" not in profile["network"]
        assert "open_port_range" not in profile["network"]

    def test_env_vars(self, runtime, worktree, git_common_dir):
        prepared(runtime, worktree)
        _, cap = run_iteration(
            runtime, worktree, git_common_dir,
            env_vars={
                "ANTHROPIC_BASE_URL": "http://127.0.0.1:18080",
                "CLAUDE_CODE_OAUTH_TOKEN": "phantom",
                "ANTHROPIC_CUSTOM_MODEL_OPTION": "claude-sonnet-4-6",
            })
        env = cap["env"]
        assert env["CLAUDE_CONFIG_DIR"] == runtime.claude_config_dir()
        assert env["GIT_CONFIG_GLOBAL"] == runtime.gitconfig_path(SANDBOX)
        assert env["DOCKER_HOST"] == f"unix://{runtime.docker_socket_path()}"
        assert env["NONO_NO_UPDATE_CHECK"] == "1"
        assert env["NONO_NO_PACK_UPDATE_HINTS"] == "1"
        assert env["NONO_NO_MIGRATE"] == "1"
        # Model settings survive; the phantom token and base URL do not.
        assert env["ANTHROPIC_CUSTOM_MODEL_OPTION"] == "claude-sonnet-4-6"
        assert "ANTHROPIC_BASE_URL" not in env
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        # The host environment is otherwise inherited.
        assert env["PATH"] == os.environ["PATH"]

    def test_all_token_vars_dropped(self, runtime, worktree, git_common_dir):
        prepared(runtime, worktree)
        _, cap = run_iteration(
            runtime, worktree, git_common_dir,
            env_vars={"ANTHROPIC_API_KEY": "phantom",
                      "ANTHROPIC_AUTH_TOKEN": "phantom"})
        assert "ANTHROPIC_API_KEY" not in cap["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in cap["env"]

    def test_spec_written_to_its_own_dir_and_removed(self, runtime, worktree,
                                                     git_common_dir):
        prepared(runtime, worktree)
        _, cap = run_iteration(runtime, worktree, git_common_dir,
                               spec="# my spec\n")
        spec_dir = cap["spec_dirs_during"][0]
        spec_path = cap["specs_during"][0]
        assert os.path.dirname(spec_dir) == runtime.state_dir(SANDBOX)
        assert os.path.basename(spec_path) == "spec.md"
        assert cap["spec_content"] == "# my spec\n"
        assert not os.path.exists(spec_dir)
        assert glob.glob(
            os.path.join(runtime.state_dir(SANDBOX), "spec-*")) == []

    def test_reads_back_updated_spec(self, runtime, worktree, git_common_dir):
        prepared(runtime, worktree)
        (rc, spec), _ = run_iteration(runtime, worktree, git_common_dir,
                                      spec="# before\n", updated="# after\n")
        assert rc == 0
        assert spec == "# after\n"

    def test_returns_exit_code(self, runtime, worktree, git_common_dir):
        prepared(runtime, worktree)
        (rc, spec), _ = run_iteration(runtime, worktree, git_common_dir,
                                      spec="# spec\n", rc=3)
        assert rc == 3
        assert spec == "# spec\n"

    def test_spec_removed_when_nono_raises(self, runtime, worktree,
                                           git_common_dir):
        prepared(runtime, worktree)
        with patch("ralph.runtime.nono.subprocess.run",
                   side_effect=OSError("boom")), \
             patch("ralph.runtime.nono.shutil.which", return_value=CLAUDE_BIN), \
             patch("ralph.runtime.nono.ephemeral_port_range",
                   return_value=PORT_RANGE), \
             patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=str(git_common_dir)):
            with pytest.raises(OSError):
                runtime.run_iteration(SANDBOX, "# spec\n", "sonnet")
        assert glob.glob(
            os.path.join(runtime.state_dir(SANDBOX), "spec-*")) == []

    def test_profile_grants_the_spec_dir(self, runtime, worktree,
                                         git_common_dir, git_dir):
        """The directory, so an atomic save (temp file + rename) works."""
        prepared(runtime, worktree)
        _, cap = run_iteration(runtime, worktree, git_common_dir)
        profile = cap["profile"]
        allow = profile["filesystem"]["allow"]
        assert cap["spec_dirs_during"][0] in allow
        assert allow[0] == str(worktree)
        assert profile["network"]["open_port_range"] == [list(PORT_RANGE)]
        assert profile["meta"]["name"] == f"ralph-{SANDBOX}"
        assert "extends" not in profile

    def test_profile_keeps_the_git_common_dir_read_only(
            self, runtime, worktree, git_common_dir, git_dir):
        """Writable .git/hooks or .git/config would run code on the host."""
        prepared(runtime, worktree)
        _, cap = run_iteration(runtime, worktree, git_common_dir)
        filesystem = cap["profile"]["filesystem"]
        assert str(git_common_dir) in filesystem["read"]
        assert str(git_common_dir) not in filesystem["allow"]
        assert str(git_dir) in filesystem["allow"]
        for subdir in ("objects", "refs", "logs"):
            assert str(git_common_dir / subdir) in filesystem["allow"]

    def test_profile_extends_project_profile(self, runtime, worktree,
                                             git_common_dir):
        prepared(runtime, worktree)
        runtime._project_profile_name = "project"
        _, cap = run_iteration(runtime, worktree, git_common_dir)
        assert cap["profile"]["extends"] == "project"

    def test_project_allowed_hosts_merged(self, home, worktree, git_common_dir):
        rt = NonoRuntime(DOTFILES, allowed_hosts=["registry.npmjs.org"])
        prepared(rt, worktree)
        with patch("ralph.runtime.nono.subprocess.run",
                   return_value=MagicMock(returncode=0)), \
             patch("ralph.runtime.nono.shutil.which", return_value=CLAUDE_BIN), \
             patch("ralph.runtime.nono.ephemeral_port_range",
                   return_value=PORT_RANGE), \
             patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=str(git_common_dir)):
            rt.run_iteration(SANDBOX, "# spec\n", "sonnet")
        with open(rt.profile_path(SANDBOX)) as f:
            profile = json.load(f)
        assert profile["network"]["allow_domain"] == [
            "api.anthropic.com", "registry.npmjs.org", "sentry.io",
            "statsig.anthropic.com",
        ]

    def test_rejects_non_claude_agent(self, runtime, worktree):
        prepared(runtime, worktree)
        with pytest.raises(ValueError, match="only supports agent claude"):
            runtime.run_iteration(SANDBOX, "# spec\n", "sonnet", agent="cursor")

    def test_requires_a_recorded_worktree(self, runtime, home):
        runtime._make_private_dir(runtime.state_dir(SANDBOX))
        with patch("ralph.runtime.nono.shutil.which", return_value=CLAUDE_BIN):
            with pytest.raises(RuntimeError, match="no worktree recorded"):
                runtime.run_iteration(SANDBOX, "# spec\n", "sonnet")

    def test_requires_claude_on_path(self, runtime, worktree, git_common_dir):
        prepared(runtime, worktree)
        with patch("ralph.runtime.nono.shutil.which", return_value=None), \
             patch("ralph.runtime.nono.ephemeral_port_range",
                   return_value=PORT_RANGE), \
             patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=str(git_common_dir)):
            with pytest.raises(RuntimeError, match="claude is not installed"):
                runtime.run_iteration(SANDBOX, "# spec\n", "sonnet")

    def test_requires_a_git_repository(self, runtime, worktree):
        prepared(runtime, worktree)
        with patch("ralph.runtime.nono.shutil.which", return_value=CLAUDE_BIN), \
             patch.object(NonoRuntime, "_resolve_git_common_dir",
                          return_value=None):
            with pytest.raises(RuntimeError, match="is not a git repository"):
                runtime.run_iteration(SANDBOX, "# spec\n", "sonnet")


# ---------------------------------------------------------------------------
# Host/sandbox sync
# ---------------------------------------------------------------------------

class TestSync:
    def test_check_in_sync_true_when_head_resolves(self, runtime):
        git = MagicMock()
        git.output.return_value = "abc123"
        assert runtime.check_in_sync(SANDBOX, "/work", git) is True
        git.output.assert_called_once_with("rev-parse", "HEAD", cwd="/work")

    def test_check_in_sync_false_when_head_missing(self, runtime):
        git = MagicMock()
        git.output.return_value = ""
        assert runtime.check_in_sync(SANDBOX, "/work", git) is False

    def test_reset_to_host(self, runtime):
        git = MagicMock()
        git.output.return_value = "abc123"
        git.run.return_value = MagicMock(returncode=0)
        assert runtime.reset_to_host(SANDBOX, "/work", git) is True
        assert git.run.call_args_list[0][0] == ("reset", "--hard", "abc123")
        assert git.run.call_args_list[1][0] == ("clean", "-fd")

    def test_reset_to_host_without_head(self, runtime):
        git = MagicMock()
        git.output.return_value = ""
        assert runtime.reset_to_host(SANDBOX, "/work", git) is False
        git.run.assert_not_called()

    def test_reset_to_host_reset_fails(self, runtime):
        git = MagicMock()
        git.output.return_value = "abc123"
        git.run.return_value = MagicMock(returncode=1)
        assert runtime.reset_to_host(SANDBOX, "/work", git) is False
        assert git.run.call_count == 1

    def test_sync_to_host_verifies_head(self, runtime, capsys):
        with patch("ralph.runtime.nono.subprocess.run",
                   return_value=MagicMock(returncode=0)) as run:
            assert runtime.sync_to_host(SANDBOX, "a", "b", "/work") is True
        assert run.call_args[0][0] == ["git", "rev-parse", "--verify", "b"]
        assert run.call_args[1]["cwd"] == "/work"
        assert "synced commits to /work" in capsys.readouterr().out

    def test_sync_to_host_missing_commit(self, runtime, capsys):
        with patch("ralph.runtime.nono.subprocess.run",
                   return_value=MagicMock(returncode=1)):
            assert runtime.sync_to_host(SANDBOX, "a", "b", "/work") is False
        assert "host cannot see commit b" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Cleanup and pruning
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_remove_sandbox_deletes_state(self, runtime, worktree):
        prepared(runtime, worktree)
        with patch.object(NonoRuntime, "_remove_sandbox_timestamp") as rm:
            runtime.remove_sandbox(SANDBOX)
        assert not os.path.exists(runtime.state_dir(SANDBOX))
        rm.assert_called_once_with(SANDBOX)

    def test_remove_sandbox_is_best_effort(self, runtime):
        with patch.object(NonoRuntime, "_remove_sandbox_timestamp"):
            runtime.remove_sandbox("agent-loop-claude-never-existed")

    def test_cleanup_sandbox_by_branch(self, runtime, worktree, capsys):
        prepared(runtime, worktree)
        with patch.object(NonoRuntime, "_remove_sandbox_timestamp"):
            runtime.cleanup_sandbox("claude", BRANCH)
        assert not os.path.exists(runtime.state_dir(SANDBOX))
        assert f"removing nono sandbox {SANDBOX}" in capsys.readouterr().out


class TestPrune:
    """Timestamps live outside the state dir, so they stay mocked here."""

    def setup_method(self):
        self.last_used = {}

    def _sandbox(self, runtime, name, worktree, age_days=0):
        prepared(runtime, worktree, name=name)
        runtime._worktree_path = None
        self.last_used[name] = time.time() - age_days * 86400
        return name

    def _prune(self, runtime, agent="claude", max_age_days=None):
        with patch.object(runtime, "_sandbox_last_used",
                          side_effect=self.last_used.get), \
             patch.object(runtime, "_remove_sandbox_timestamp"):
            return runtime.prune_sandboxes(agent, max_age_days=max_age_days)

    def test_prunes_orphan_with_missing_worktree(self, runtime, tmp_path):
        gone = tmp_path / "gone"
        gone.mkdir()
        name = self._sandbox(runtime, "agent-loop-claude-orphan", gone)
        gone.rmdir()
        assert self._prune(runtime) == [name]
        assert not os.path.exists(runtime.state_dir(name))

    def test_prunes_sandbox_without_worktree_file(self, runtime):
        runtime._make_private_dir(runtime.state_dir("agent-loop-claude-bare"))
        assert self._prune(runtime) == ["agent-loop-claude-bare"]

    def test_keeps_fresh_sandbox(self, runtime, worktree):
        self._sandbox(runtime, "agent-loop-claude-fresh", worktree)
        assert self._prune(runtime) == []
        assert os.path.isdir(runtime.state_dir("agent-loop-claude-fresh"))

    def test_prunes_stale_sandbox(self, runtime, worktree):
        name = self._sandbox(runtime, "agent-loop-claude-stale", worktree,
                             age_days=5)
        assert self._prune(runtime) == [name]
        assert not os.path.exists(runtime.state_dir(name))

    def test_max_age_days_override(self, runtime, worktree):
        name = self._sandbox(runtime, "agent-loop-claude-old", worktree,
                             age_days=1)
        assert self._prune(runtime) == []
        assert self._prune(runtime, max_age_days=0) == [name]

    def test_prunes_sandbox_without_timestamp(self, runtime, worktree):
        name = self._sandbox(runtime, "agent-loop-claude-untracked", worktree)
        del self.last_used[name]
        assert self._prune(runtime) == [name]

    def test_ignores_other_agents(self, runtime, tmp_path):
        gone = tmp_path / "gone"
        gone.mkdir()
        self._sandbox(runtime, "agent-loop-cursor-orphan", gone)
        gone.rmdir()
        assert self._prune(runtime) == []
        assert os.path.isdir(runtime.state_dir("agent-loop-cursor-orphan"))

    def test_no_state_root_is_not_an_error(self, runtime):
        assert self._prune(runtime) == []


# ---------------------------------------------------------------------------
# Token store command
# ---------------------------------------------------------------------------

class TestTokenStoreCommand:
    """The command the selftest runs *inside* the sandbox.

    It must be the same one ralph uses on the host, or the selftest would
    prove the sandbox cannot read some command nothing uses.
    """

    def test_darwin(self, runtime, monkeypatch):
        monkeypatch.setattr("ralph.token.sys.platform", "darwin")
        assert runtime._token_store_read_command() == [
            "security", "find-generic-password",
            "-s", "claude-token", "-a", "agent-loop", "-w"]

    def test_linux(self, runtime, monkeypatch):
        monkeypatch.setattr("ralph.token.sys.platform", "linux")
        assert runtime._token_store_read_command() == [
            "secret-tool", "lookup", "service", "claude-token",
            "account", "agent-loop"]

    def test_follows_auth_mode(self, home, git_common_dir, monkeypatch):
        monkeypatch.setattr("ralph.token.sys.platform", "linux")
        rt = NonoRuntime(DOTFILES, auth_mode="api-key")
        assert "claude-api-key" in rt._token_store_read_command()

    def test_matches_the_host_read_command(self, runtime):
        service = keychain_service_name("claude", None)
        assert runtime._token_store_read_command() == keystore_read_command(
            service)
