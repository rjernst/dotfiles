"""Repo resolution, dependency checking, worktree management, fast-forward."""

import os
import re
import shutil
import subprocess
import sys

from dotlib.git import Git
from ralph.github import GitHub
from ralph.util import parse_frontmatter


def resolve_repo(git):
    """Resolve owner/repo from origin remote URL."""
    url = git.output("remote", "get-url", "origin")
    if not url:
        return None
    # Strip git@github.com: or https://github.com/ prefix and .git suffix
    url = re.sub(r'.*github\.com[:/]', '', url)
    url = re.sub(r'\.git$', '', url)
    return url


def check_dependencies(deps, repo, gh=None):
    """Check if all dependency issues have status:done.
    Returns list of unmet dependency numbers.
    """
    if gh is None:
        gh = GitHub()
    unmet = []
    for num in deps:
        try:
            labels = gh.issue_view_labels(int(num), repo)
            if "status:done" not in labels:
                unmet.append(num)
        except Exception:
            print(f"ralph: warning: could not fetch issue #{num}", file=sys.stderr)
            unmet.append(num)
    return unmet


def unblock_ready_specs(repo, gh=None):
    """Scan blocked specs and unblock those with satisfied dependencies."""
    if gh is None:
        gh = GitHub()
    try:
        numbers = gh.issue_list(repo, ["status:blocked", "spec"])
    except (subprocess.CalledProcessError, Exception):
        print("ralph: warning: failed to fetch blocked issues", file=sys.stderr)
        return

    for number in numbers:
        try:
            body = gh.issue_view_body(number, repo)
        except Exception:
            continue

        depends = parse_frontmatter(body, "depends")

        if not depends:
            print(f"ralph: unblocked issue #{number} — no dependencies declared")
            gh.issue_edit(number, repo,
                          remove_labels="status:blocked",
                          add_label="status:ready")
            continue

        dep_list = depends.split()
        unmet = check_dependencies(dep_list, repo, gh)
        if not unmet:
            print(f"ralph: unblocked issue #{number} — all dependencies met")
            gh.issue_edit(number, repo,
                          remove_labels="status:blocked",
                          add_label="status:ready")


def ensure_worktree(git, branch, base=None):
    """Find or create a git worktree for the given branch."""
    repo_root = git.output("rev-parse", "--show-toplevel")
    sanitized_branch = branch.replace('/', '-')
    parent = os.path.dirname(repo_root)
    repo_name = os.path.basename(repo_root)
    wt_path = os.path.join(parent, f"{repo_name}-{sanitized_branch}")

    # Check if worktree already exists for this branch
    try:
        result = git.run("worktree", "list", "--porcelain")
        porcelain = result.stdout
    except subprocess.CalledProcessError:
        porcelain = ""

    lines = porcelain.splitlines()
    lines.append("")  # ensure trailing blank to flush last entry
    wt_dir = ""
    wt_branch = ""
    for line in lines:
        if line.startswith("worktree "):
            wt_dir = line[len("worktree "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            if ref.startswith("refs/heads/"):
                wt_branch = ref[len("refs/heads/"):]
            else:
                wt_branch = ref
        elif line == "":
            if wt_branch == branch:
                return wt_dir
            wt_dir = ""
            wt_branch = ""

    # Determine remote (upstream > origin)
    remote = ""
    remotes_raw = git.output("remote")
    remotes = remotes_raw.splitlines() if remotes_raw else []
    if "upstream" in remotes:
        remote = "upstream"
    elif "origin" in remotes:
        remote = "origin"

    # Determine default branch
    default_branch = ""
    if remote:
        ref = git.output("symbolic-ref", f"refs/remotes/{remote}/HEAD")
        if ref:
            default_branch = ref.replace(f"refs/remotes/{remote}/", "")

    if not default_branch or git.run("rev-parse", "--verify", default_branch, check=False).returncode != 0:
        default_branch = git.output("symbolic-ref", "--short", "HEAD") or "HEAD"

    # Override with explicit base
    if base:
        default_branch = base

    # Create worktree
    local_exists = git.run(
        "rev-parse", "--verify", f"refs/heads/{branch}", check=False,
    ).returncode == 0

    if local_exists:
        git.run("worktree", "add", wt_path, branch)
    elif remote:
        ls_result = git.run(
            "ls-remote", "--exit-code", "--heads", remote, f"refs/heads/{branch}",
            check=False,
        )
        if ls_result.returncode == 0:
            git.run("worktree", "add", "--track", "-b", branch, wt_path,
                     f"{remote}/{branch}")
        else:
            git.run("worktree", "add", "-b", branch, wt_path, default_branch)
    else:
        git.run("worktree", "add", "-b", branch, wt_path, default_branch)
    return wt_path


def try_fast_forward(git, work_dir, base=None):
    """Try to fast-forward the current branch to the base branch.

    Reduces future merge conflicts by incorporating latest upstream changes
    before the agent starts working. Only advances the branch if a clean
    fast-forward is possible (no divergent commits).
    """
    remotes_raw = git.output("remote", cwd=work_dir)
    remotes = remotes_raw.splitlines() if remotes_raw else []
    if "upstream" in remotes:
        remote = "upstream"
    elif "origin" in remotes:
        remote = "origin"
    else:
        return None

    if not base:
        ref = git.output("symbolic-ref", f"refs/remotes/{remote}/HEAD",
                         cwd=work_dir)
        if ref:
            base = ref.replace(f"refs/remotes/{remote}/", "")
    if not base:
        return None

    result = git.run("fetch", remote, base, cwd=work_dir, check=False)
    if result.returncode != 0:
        return None

    remote_ref = f"{remote}/{base}"
    result = git.run("merge", "--ff-only", remote_ref, cwd=work_dir,
                     check=False)
    if result.returncode == 0:
        return remote_ref
    return None


def check_dependencies_prereq():
    """Check that required tools are on PATH."""
    if not shutil.which("docker"):
        print("ralph: docker is not installed", file=sys.stderr)
        sys.exit(1)
    if not shutil.which("gh"):
        print("ralph: gh is not installed (install: brew install gh)", file=sys.stderr)
        sys.exit(1)
