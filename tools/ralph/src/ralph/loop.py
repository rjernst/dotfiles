"""Issue processing loop and poll mode."""

import signal
import sys
import time

from ralph.agents import get_agent
from ralph.github import GitHub
from ralph.orchestration import (
    resolve_repo, check_dependencies, unblock_ready_specs,
    ensure_worktree, try_fast_forward,
)
from ralph.proxy import MODEL_ALIASES, proxy_health_check, ensure_proxy
from ralph.runtime import load_runtime_config, create_runtime
from ralph.util import parse_frontmatter, parse_issue_branch


def process_issue(issue_number, git, dotfiles_dir, gh, agent, push, model,
                  git_user, git_email, proxy_port, token, rebuild=False):
    """Process a single GitHub Issue spec."""
    repo = resolve_repo(git)
    if not repo:
        print("ralph: could not detect repo from origin remote", file=sys.stderr)
        return 1

    # Fetch issue data
    try:
        title = gh.issue_view_title(issue_number, repo)
    except Exception:
        print(f"ralph: failed to fetch issue #{issue_number} from {repo}", file=sys.stderr)
        return 1

    try:
        body = gh.issue_view_body(issue_number, repo)
    except Exception:
        print(f"ralph: failed to fetch issue #{issue_number} from {repo}", file=sys.stderr)
        return 1

    # Parse branch
    branch = parse_frontmatter(body, "branch")
    if not branch:
        print("ralph: no branch in frontmatter, falling back to title", file=sys.stderr)
        branch = parse_issue_branch(title)
        if not branch:
            return 1

    base = parse_frontmatter(body, "base")

    # Dependency check
    depends = parse_frontmatter(body, "depends")
    if depends:
        dep_list = depends.split()
        unmet = check_dependencies(dep_list, repo, gh)
        if unmet:
            unmet_str = ' '.join(unmet)
            print(f"ralph: issue #{issue_number} has unmet dependencies: {unmet_str}")
            print(f"ralph: transitioning issue #{issue_number} to status:blocked")
            gh.issue_edit(issue_number, repo,
                          remove_labels="status:ready",
                          add_label="status:blocked")
            return 0

    # Ensure worktree
    work_dir = ensure_worktree(git, branch, base)

    # Store issue number in git config for local tracking (used by /merge
    # to find the associated spec issue when closing after merge)
    result = git.run("config", f"branch.{branch}.issue", str(issue_number),
                     cwd=work_dir, check=False)
    if result.returncode != 0:
        print(f"ralph: warning: failed to store issue number in git config "
              f"for branch {branch}", file=sys.stderr)

    print(f"ralph: processing issue #{issue_number} on branch {branch}")
    print(f"ralph: using worktree at {work_dir}")

    # Fast-forward to base branch if possible to reduce future merge conflicts
    ff_ref = try_fast_forward(git, work_dir, base)
    if ff_ref:
        print(f"ralph: fast-forwarded {branch} to {ff_ref}")

    # Resolve project root for project-level sandbox dependencies
    repo_root = git.output("rev-parse", "--show-toplevel")

    # Create runtime backend based on project config
    config = load_runtime_config(repo_root)
    config["project_dir"] = repo_root
    runtime_type = config.pop("type")
    runtime = create_runtime(runtime_type, dotfiles_dir, **config)

    # Auto-prune stale sandboxes before creating/reusing ours
    try:
        pruned = runtime.prune_sandboxes(agent)
        if pruned:
            print(f"ralph: pruned {len(pruned)} stale sandbox(es)")
    except Exception:
        pass  # best-effort — don't block issue processing

    # Ensure sandbox
    sandbox_name = runtime.ensure_sandbox(agent, branch, work_dir,
                                          project_dir=repo_root,
                                          force_rebuild=rebuild)

    # Configure git inside sandbox
    runtime.setup_git_config(sandbox_name, git_user, git_email)

    # Ensure sandbox can access the shared git state
    if not runtime.check_in_sync(sandbox_name, work_dir, git):
        print("ralph: sandbox out of sync with host, resetting...")
        if runtime.reset_to_host(sandbox_name, work_dir, git):
            print("ralph: sandbox reset to match host")
        else:
            print("ralph: reset failed, recreating sandbox...")
            runtime.remove_sandbox(sandbox_name)
            sandbox_name = runtime.ensure_sandbox(agent, branch, work_dir,
                                                  project_dir=repo_root,
                                                  force_rebuild=rebuild)
            runtime.setup_git_config(sandbox_name, git_user, git_email)

    # Label in-progress
    gh.issue_edit(issue_number, repo,
                  remove_labels="status:ready",
                  add_label="status:in-progress")

    # Build env vars and API key based on agent type.
    # The token was already resolved by ensure_token() in cli.py and
    # passed through — no need to re-read from Keychain.
    agent_config = get_agent(agent)
    if agent_config["uses_proxy"]:
        # Real token stays in proxy — sandbox only sees a phantom token
        # and the proxy's base URL.  ANTHROPIC_CUSTOM_MODEL_OPTION bypasses
        # client-side model validation which otherwise fails because the
        # phantom token has no subscription tier metadata.
        model_id = MODEL_ALIASES.get(model, model)
        env_vars = {
            "CLAUDE_CODE_OAUTH_TOKEN": "phantom",
            "ANTHROPIC_BASE_URL": f"http://{runtime.proxy_host()}:{proxy_port}",
            "ANTHROPIC_CUSTOM_MODEL_OPTION": model_id,
        }
        api_key = None
    else:
        # Non-proxy agents: API key is delivered via secret file in
        # run_iteration — no env vars passed via docker exec -e.
        env_vars = {}
        api_key = token

    try:
        while True:
            # Record HEAD before iteration — shared .git means host sees
            # the same commits as the sandbox
            head_before = git.output("rev-parse", "HEAD", cwd=work_dir)

            # Run iteration
            print(f"ralph: starting iteration (sandbox={sandbox_name}, issue=#{issue_number}, model={model})")
            rc, body = runtime.run_iteration(sandbox_name, body, model,
                                             env_vars, agent=agent,
                                             api_key=api_key)
            if rc != 0:
                # For proxy-based agents, check if failure was caused by
                # the proxy being down (e.g. idle timeout after sleep)
                if agent_config["uses_proxy"]:
                    healthy, _ = proxy_health_check(proxy_port)
                    if not healthy:
                        print("ralph: proxy died during iteration, restarting and retrying...")
                        ensure_proxy(agent, proxy_port, dotfiles_dir)
                        continue
                print(f"ralph: iteration failed for issue #{issue_number}", file=sys.stderr)
                gh.issue_edit(issue_number, repo,
                              remove_labels="status:in-progress",
                              add_label="status:needs-attention")
                return 1

            # Check if work was done
            head_after = git.output("rev-parse", "HEAD", cwd=work_dir)

            if head_before == head_after:
                if "[blocked:" in body:
                    print(f"ralph: blocked tasks found in issue #{issue_number}, marking needs-attention")
                    gh.issue_edit(issue_number, repo,
                                  remove_labels="status:in-progress",
                                  add_label="status:needs-attention")
                else:
                    print(f"ralph: no commit made, marking issue #{issue_number} done")
                    gh.issue_edit(issue_number, repo,
                                  remove_labels="status:in-progress",
                                  add_label="status:done")
                    unblock_ready_specs(repo, gh)
                    runtime.cleanup_sandbox(agent, branch)
                break

            # Sync commits from sandbox to host worktree
            if not runtime.sync_to_host(sandbox_name, head_before, head_after, work_dir):
                print(f"ralph: sync failed, marking issue #{issue_number} needs-attention",
                      file=sys.stderr)
                gh.issue_edit(issue_number, repo,
                              remove_labels="status:in-progress",
                              add_label="status:needs-attention")
                return 1

            # Update issue body from spec
            gh.issue_edit(issue_number, repo, body=body)
            print(f"ralph: iteration complete, updated issue #{issue_number}")

            # Optional push (from host, after sync)
            if push:
                git.run("push", cwd=work_dir, check=False)
    except KeyboardInterrupt:
        print(f"\nralph: interrupted, restoring issue #{issue_number} to ready")
        prev_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            gh.issue_edit(issue_number, repo,
                          remove_labels="status:in-progress",
                          add_label="status:ready")
        except Exception:
            pass
        finally:
            signal.signal(signal.SIGINT, prev_handler)
        raise

    return 0


def poll_loop(git, dotfiles_dir, gh, agent, push, model, git_user, git_email,
              proxy_port, token, interval, timeout, rebuild=False):
    """Poll for ready issues and process them."""
    repo = resolve_repo(git)
    if not repo:
        print("ralph: could not detect repo from origin remote", file=sys.stderr)
        sys.exit(1)

    deadline = 0
    if timeout > 0:
        deadline = time.time() + timeout

    last_was_idle = False

    try:
        while True:
            # Check deadline
            if deadline > 0 and time.time() >= deadline:
                if last_was_idle:
                    sys.stdout.write("\n")
                print("ralph: poll timeout reached")
                break

            # Unblock specs
            unblock_ready_specs(repo, gh)

            # Fetch ready issues
            try:
                numbers = gh.issue_list(repo, ["spec", "status:ready"], author="@me")
            except Exception:
                if last_was_idle:
                    sys.stdout.write("\n")
                print(f"ralph: failed to list issues from {repo}", file=sys.stderr)
                last_was_idle = False
                time.sleep(interval)
                continue

            if numbers:
                if last_was_idle:
                    sys.stdout.write("\n")
                last_was_idle = False
                for num in numbers:
                    print(f"ralph: found ready issue #{num}")
                    try:
                        process_issue(num, git, dotfiles_dir, gh, agent, push, model,
                                      git_user, git_email, proxy_port,
                                      token, rebuild=rebuild)
                    except Exception as exc:
                        print(f"ralph: unexpected error processing issue #{num}: {exc}",
                              file=sys.stderr)
                        try:
                            gh.issue_edit(num, repo,
                                          remove_labels=["status:ready",
                                                         "status:in-progress"],
                                          add_label="status:needs-attention")
                        except Exception:
                            pass
            else:
                timestamp = time.strftime("%H:%M:%S")
                sys.stdout.write(f"\rralph: no ready issues found (last checked at {timestamp})\033[K")
                sys.stdout.flush()
                last_was_idle = True

            # Check deadline before sleeping
            if deadline > 0 and time.time() >= deadline:
                if last_was_idle:
                    sys.stdout.write("\n")
                print("ralph: poll timeout reached")
                break

            time.sleep(interval)
    except KeyboardInterrupt:
        if last_was_idle:
            sys.stdout.write("\n")
        print("ralph: poll interrupted")
