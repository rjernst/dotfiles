"""Tests for ralph.orchestration — repo resolution, dependencies, worktree, fast-forward."""

import subprocess
from unittest.mock import MagicMock, patch

from ralph.orchestration import (
    check_dependencies, unblock_ready_specs, ensure_worktree, try_fast_forward,
)


# ---------------------------------------------------------------------------
# check_dependencies (mocked GitHub)
# ---------------------------------------------------------------------------

class TestCheckDependencies:
    def test_all_deps_done_returns_empty(self):
        gh = MagicMock()
        gh.issue_view_labels.return_value = ["spec", "status:done"]
        result = check_dependencies(["11", "17"], "owner/repo", gh)
        assert result == []

    def test_some_deps_not_done_returns_unmet(self):
        gh = MagicMock()
        def labels_side_effect(num, repo):
            if int(num) == 11:
                return ["spec", "status:done"]
            return ["spec", "status:in-progress"]
        gh.issue_view_labels.side_effect = labels_side_effect
        result = check_dependencies(["11", "17"], "owner/repo", gh)
        assert result == ["17"]

    def test_gh_failure_treats_dep_as_unmet(self):
        gh = MagicMock()
        gh.issue_view_labels.side_effect = subprocess.CalledProcessError(1, "gh")
        result = check_dependencies(["11"], "owner/repo", gh)
        assert "11" in result


# ---------------------------------------------------------------------------
# unblock_ready_specs (mocked GitHub)
# ---------------------------------------------------------------------------

class TestUnblockReadySpecs:
    def test_transitions_blocked_to_ready_when_deps_met(self):
        gh = MagicMock()
        gh.issue_list.return_value = [5]
        gh.issue_view_body.return_value = "---\ndepends: [11]\n---\nSome spec"
        gh.issue_view_labels.return_value = ["spec", "status:done"]

        unblock_ready_specs("owner/repo", gh)

        gh.issue_edit.assert_called_once_with(
            5, "owner/repo",
            remove_label="status:blocked",
            add_label="status:ready",
        )

    def test_leaves_blocked_when_deps_unmet(self):
        gh = MagicMock()
        gh.issue_list.return_value = [5]
        gh.issue_view_body.return_value = "---\ndepends: [11]\n---\nSome spec"
        gh.issue_view_labels.return_value = ["spec", "status:in-progress"]

        unblock_ready_specs("owner/repo", gh)

        gh.issue_edit.assert_not_called()

    def test_unblocks_spec_with_no_depends_field(self):
        gh = MagicMock()
        gh.issue_list.return_value = [8]
        gh.issue_view_body.return_value = "---\nbranch: my-branch\n---\nSome spec with no depends"

        unblock_ready_specs("owner/repo", gh)

        gh.issue_edit.assert_called_once_with(
            8, "owner/repo",
            remove_label="status:blocked",
            add_label="status:ready",
        )


# ---------------------------------------------------------------------------
# ensure_worktree
# ---------------------------------------------------------------------------

def _mock_git_for_worktree(*, remotes="origin", porcelain="",
                           toplevel="/Users/me/code/myrepo",
                           symbolic_ref="refs/remotes/origin/main",
                           default_branch_name="main",
                           rev_parse_verify_ok=True,
                           ls_remote_ok=False,
                           local_branch_exists=False):
    """Build a MagicMock Git instance with side-effects for ensure_worktree."""
    git = MagicMock()

    # git.output dispatches on first arg
    def output_side_effect(*args, **kwargs):
        if args[0] == "rev-parse" and args[1] == "--show-toplevel":
            return toplevel
        if args[0] == "remote":
            return remotes
        if args[0] == "symbolic-ref":
            # "symbolic-ref refs/remotes/<remote>/HEAD" → full ref
            if len(args) > 1 and "remotes" in args[1]:
                return symbolic_ref
            # "symbolic-ref --short HEAD" → short branch name
            return default_branch_name
        return ""
    git.output.side_effect = output_side_effect

    # git.run dispatches on first arg
    def run_side_effect(*args, **kwargs):
        check = kwargs.get("check", True)
        if args[0] == "worktree" and args[1] == "list":
            return MagicMock(stdout=porcelain)
        if args[0] == "rev-parse" and args[1] == "--verify":
            if "refs/heads/" in args[2]:
                # Checking if a local branch exists
                rc = 0 if local_branch_exists else 128
            else:
                # Verifying default branch name is valid
                rc = 0 if rev_parse_verify_ok else 128
            result = MagicMock(returncode=rc)
            if check and rc != 0:
                raise subprocess.CalledProcessError(rc, "git")
            return result
        if args[0] == "ls-remote":
            rc = 0 if ls_remote_ok else 2
            return MagicMock(returncode=rc)
        # worktree add, etc. — just succeed
        return MagicMock(returncode=0)
    git.run.side_effect = run_side_effect

    return git


class TestEnsureWorktree:
    def test_returns_existing_worktree(self):
        """If a worktree already exists for the branch, return its path."""
        porcelain = (
            "worktree /Users/me/code/myrepo\n"
            "branch refs/heads/main\n"
            "\n"
            "worktree /Users/me/code/myrepo-my-feature\n"
            "branch refs/heads/my-feature\n"
            "\n"
        )
        git = _mock_git_for_worktree(porcelain=porcelain)

        result = ensure_worktree(git, "my-feature")
        assert result == "/Users/me/code/myrepo-my-feature"
        # Should not call worktree add
        for c in git.run.call_args_list:
            assert c[0][0:2] != ("worktree", "add")

    def test_creates_new_branch_from_default(self):
        """No remote branch, no local branch — creates new branch from default."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)

        result = ensure_worktree(git, "new-feature")
        assert result == "/Users/me/code/myrepo-new-feature"
        git.run.assert_any_call(
            "worktree", "add", "-b", "new-feature",
            "/Users/me/code/myrepo-new-feature", "main")

    def test_tracks_remote_branch(self):
        """Remote branch exists — creates tracking worktree."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=True,
                                     local_branch_exists=False)

        result = ensure_worktree(git, "remote-feature")
        assert result == "/Users/me/code/myrepo-remote-feature"
        git.run.assert_any_call(
            "worktree", "add", "--track", "-b", "remote-feature",
            "/Users/me/code/myrepo-remote-feature", "origin/remote-feature")

    def test_uses_existing_local_branch(self):
        """Local branch exists but no worktree — attaches without -b."""
        git = _mock_git_for_worktree(remotes="origin", local_branch_exists=True)

        result = ensure_worktree(git, "existing-branch")
        assert result == "/Users/me/code/myrepo-existing-branch"
        git.run.assert_any_call(
            "worktree", "add",
            "/Users/me/code/myrepo-existing-branch", "existing-branch")

    def test_prefers_upstream_over_origin(self):
        """When both upstream and origin exist, use upstream."""
        git = _mock_git_for_worktree(
            remotes="origin\nupstream", ls_remote_ok=False,
            local_branch_exists=False,
            symbolic_ref="refs/remotes/upstream/main",
        )

        ensure_worktree(git, "feat")
        # Should resolve default branch via upstream
        git.output.assert_any_call("symbolic-ref", "refs/remotes/upstream/HEAD")

    def test_no_remote_creates_branch_from_head(self):
        """No remotes — falls back to HEAD for base branch."""
        git = _mock_git_for_worktree(remotes="", local_branch_exists=False)

        result = ensure_worktree(git, "solo-feature")
        assert result == "/Users/me/code/myrepo-solo-feature"
        git.run.assert_any_call(
            "worktree", "add", "-b", "solo-feature",
            "/Users/me/code/myrepo-solo-feature", "main")

    def test_base_override(self):
        """Explicit base overrides the resolved default branch."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)

        result = ensure_worktree(git, "feat", base="develop")
        assert result == "/Users/me/code/myrepo-feat"
        git.run.assert_any_call(
            "worktree", "add", "-b", "feat",
            "/Users/me/code/myrepo-feat", "develop")

    def test_slash_in_branch_name_sanitized(self):
        """Slashes in branch names are replaced with hyphens in the path."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)

        result = ensure_worktree(git, "user/my-feature")
        assert result == "/Users/me/code/myrepo-user-my-feature"

    def test_worktree_list_failure_treated_as_empty(self):
        """If git worktree list fails, treat as no existing worktrees."""
        git = _mock_git_for_worktree(remotes="origin", ls_remote_ok=False,
                                     local_branch_exists=False)
        # Override worktree list to raise
        original_side_effect = git.run.side_effect
        def run_with_wt_failure(*args, **kwargs):
            if args[0] == "worktree" and args[1] == "list":
                raise subprocess.CalledProcessError(1, "git")
            return original_side_effect(*args, **kwargs)
        git.run.side_effect = run_with_wt_failure

        result = ensure_worktree(git, "new-feat")
        assert result == "/Users/me/code/myrepo-new-feat"


# ---------------------------------------------------------------------------
# try_fast_forward
# ---------------------------------------------------------------------------

def _mock_git_for_ff(*, remotes="origin",
                     symbolic_ref="refs/remotes/origin/main",
                     fetch_ok=True, merge_ok=True):
    """Build a MagicMock Git instance for try_fast_forward tests."""
    git = MagicMock()

    def output_side_effect(*args, **kwargs):
        if args[0] == "remote":
            return remotes
        if args[0] == "symbolic-ref":
            return symbolic_ref
        return ""
    git.output.side_effect = output_side_effect

    def run_side_effect(*args, **kwargs):
        if args[0] == "fetch":
            return MagicMock(returncode=0 if fetch_ok else 1)
        if args[0] == "merge":
            return MagicMock(returncode=0 if merge_ok else 1)
        return MagicMock(returncode=0)
    git.run.side_effect = run_side_effect

    return git


class TestTryFastForward:
    def test_fast_forwards_to_main(self):
        git = _mock_git_for_ff()
        result = try_fast_forward(git, "/work/my-branch")
        assert result == "origin/main"
        git.run.assert_any_call("fetch", "origin", "main",
                                cwd="/work/my-branch", check=False)
        git.run.assert_any_call("merge", "--ff-only", "origin/main",
                                cwd="/work/my-branch", check=False)

    def test_uses_explicit_base(self):
        git = _mock_git_for_ff()
        result = try_fast_forward(git, "/work/feat", base="8.x")
        assert result == "origin/8.x"
        git.run.assert_any_call("fetch", "origin", "8.x",
                                cwd="/work/feat", check=False)

    def test_prefers_upstream(self):
        git = _mock_git_for_ff(remotes="origin\nupstream",
                               symbolic_ref="refs/remotes/upstream/main")
        result = try_fast_forward(git, "/work/feat")
        assert result == "upstream/main"

    def test_returns_none_when_no_remote(self):
        git = _mock_git_for_ff(remotes="")
        result = try_fast_forward(git, "/work/feat")
        assert result is None

    def test_returns_none_when_fetch_fails(self):
        git = _mock_git_for_ff(fetch_ok=False)
        result = try_fast_forward(git, "/work/feat")
        assert result is None

    def test_returns_none_when_not_ff(self):
        """Branch has diverged — merge --ff-only fails, returns None."""
        git = _mock_git_for_ff(merge_ok=False)
        result = try_fast_forward(git, "/work/feat")
        assert result is None

    def test_returns_none_when_no_default_branch_detected(self):
        git = _mock_git_for_ff(symbolic_ref="")
        result = try_fast_forward(git, "/work/feat")
        assert result is None
