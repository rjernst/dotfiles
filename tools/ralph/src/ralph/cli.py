"""CLI argument parsing and main() dispatch."""

import sys

from dotlib import DOTFILES_DIR
from dotlib.git import Git
from ralph.github import GitHub
from ralph.loop import process_issue, poll_loop
from ralph.agents import get_agent
from ralph.orchestration import check_dependencies_prereq
from ralph.proxy import ensure_proxy, proxy_port_for_agent, start_proxy_keepalive
from ralph.runtime import create_runtime
from ralph.selftest import selftest
from ralph.token import store_token, check_token, get_token, ensure_token
from ralph.util import parse_duration


USAGE_TEXT = """\
Usage: ralph <command> [options]

Token commands:
  store-token           Run claude setup-token and store in Keychain
                        (or read from stdin when piped)
  check-token           Check if stored token is valid
  get-token             Print stored token to stdout

Runtime commands:
  selftest              Smoke test the full pipeline (proxy, sandbox, auth)
    --runtime <type>      Runtime type: docker-sandbox, docker-container, tart
  prune-sandboxes       Remove orphaned and stale sandboxes
    --runtime <type>      Runtime type: docker-sandbox, docker-container, tart

Issue commands:
  --issue <number>      Execute a single GitHub Issue spec
  --poll                Poll for status:ready issues and process them

Options:
  --agent <name>        Agent name (default: claude)
  --auth <oauth|api-key|gateway> Auth mode (default: oauth for claude; ignored for cursor)
  --interval <duration> Poll interval (default: 30s, requires --poll)
  --timeout <duration>  Limit poll duration (e.g. 30m, 4h, 1d; requires --poll)
  --push                Git push after each iteration
  --rebuild             Force re-pull base image and rebuild sandbox
  --model <model>       Model name (default: per-agent, e.g. sonnet for claude)
  -h, --help            Show usage"""


_VALID_AUTH_CLI = {"oauth", "api-key", "api_key", "gateway"}


def _parse_auth_mode(value):
    """Validate and normalize an --auth value.

    Accepts 'oauth', 'api-key', 'api_key', or 'gateway'.  Returns the
    internal form ('oauth', 'api_key', or 'gateway').  Exits 2 on invalid input.
    """
    if value not in _VALID_AUTH_CLI:
        print(
            f"ralph: unknown auth mode: {value} (expected: oauth, api-key, gateway)",
            file=sys.stderr,
        )
        sys.exit(2)
    return value.replace("-", "_")


def usage(exit_code=0):
    """Print usage and exit."""
    print(USAGE_TEXT)
    sys.exit(exit_code)


def main():
    args = sys.argv[1:]

    # Token management subcommands — handle before flag parsing
    # Supports both: ralph store-token --agent codex
    #            and: ralph --agent codex store-token
    token_subcommands = {"store-token", "check-token", "get-token"}
    subcmd = None
    for a in args:
        if a in token_subcommands:
            subcmd = a
            break

    if subcmd is not None:
        agent = "claude"
        auth_mode = None
        rest = [a for a in args if a != subcmd]
        j = 0
        while j < len(rest):
            if rest[j] == "--agent":
                if j + 1 >= len(rest):
                    print("ralph: --agent requires an argument", file=sys.stderr)
                    sys.exit(2)
                agent = rest[j + 1]
                j += 2
            elif rest[j] == "--auth":
                if j + 1 >= len(rest):
                    print("ralph: --auth requires an argument", file=sys.stderr)
                    sys.exit(2)
                auth_mode = _parse_auth_mode(rest[j + 1])
                j += 2
            elif rest[j] in ("-h", "--help"):
                usage(0)
            else:
                print(f"ralph: unknown option for {subcmd}: {rest[j]}", file=sys.stderr)
                sys.exit(2)

        if subcmd == "store-token":
            store_token(agent, auth_mode)
        elif subcmd == "check-token":
            check_token(agent, auth_mode)
        elif subcmd == "get-token":
            get_token(agent, auth_mode)
        sys.exit(0)

    # Runtime subcommands
    if args and args[0] == "prune-sandboxes":
        agent = "claude"
        runtime_type = "docker-sandbox"
        max_age_days = None
        rest = args[1:]
        j = 0
        while j < len(rest):
            if rest[j] == "--agent":
                if j + 1 >= len(rest):
                    print("ralph: --agent requires an argument", file=sys.stderr)
                    sys.exit(2)
                agent = rest[j + 1]
                j += 2
            elif rest[j] == "--runtime":
                if j + 1 >= len(rest):
                    print("ralph: --runtime requires an argument", file=sys.stderr)
                    sys.exit(2)
                runtime_type = rest[j + 1]
                if runtime_type not in ("docker-sandbox", "docker-container",
                                        "tart"):
                    print(f"ralph: unknown runtime type: {runtime_type}",
                          file=sys.stderr)
                    sys.exit(2)
                j += 2
            elif rest[j] == "--max-age":
                if j + 1 >= len(rest):
                    print("ralph: --max-age requires an argument", file=sys.stderr)
                    sys.exit(2)
                try:
                    max_age_days = int(rest[j + 1])
                except ValueError:
                    print(f"ralph: --max-age must be an integer: {rest[j + 1]}",
                          file=sys.stderr)
                    sys.exit(2)
                j += 2
            elif rest[j] in ("-h", "--help"):
                usage(0)
            else:
                print(f"ralph: unknown option for prune-sandboxes: {rest[j]}",
                      file=sys.stderr)
                sys.exit(2)

        runtime = create_runtime(runtime_type, DOTFILES_DIR)
        pruned = runtime.prune_sandboxes(agent, max_age_days=max_age_days)
        if not pruned:
            print("ralph: no stale sandboxes found")
        sys.exit(0)

    # Selftest subcommand
    if args and args[0] == "selftest":
        agent = "claude"
        auth_mode = None
        runtime_type = "docker-sandbox"
        rest = args[1:]
        j = 0
        while j < len(rest):
            if rest[j] == "--agent":
                if j + 1 >= len(rest):
                    print("ralph: --agent requires an argument", file=sys.stderr)
                    sys.exit(2)
                agent = rest[j + 1]
                j += 2
            elif rest[j] == "--auth":
                if j + 1 >= len(rest):
                    print("ralph: --auth requires an argument", file=sys.stderr)
                    sys.exit(2)
                auth_mode = _parse_auth_mode(rest[j + 1])
                j += 2
            elif rest[j] == "--runtime":
                if j + 1 >= len(rest):
                    print("ralph: --runtime requires an argument", file=sys.stderr)
                    sys.exit(2)
                runtime_type = rest[j + 1]
                if runtime_type not in ("docker-sandbox", "docker-container",
                                        "tart"):
                    print(f"ralph: unknown runtime type: {runtime_type}",
                          file=sys.stderr)
                    sys.exit(2)
                j += 2
            elif rest[j] in ("-h", "--help"):
                usage(0)
            else:
                print(f"ralph: unknown option for selftest: {rest[j]}",
                      file=sys.stderr)
                sys.exit(2)

        check_dependencies_prereq()
        sys.exit(selftest(agent, DOTFILES_DIR, runtime_type=runtime_type,
                          auth_mode=auth_mode))

    # Parse arguments manually to match zsh behavior exactly
    push = False
    rebuild = False
    timeout_val = 0
    model = None
    issue_number = ""
    poll = False
    interval = 30
    agent = "claude"
    auth_mode = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--push":
            push = True
            i += 1
        elif arg == "--rebuild":
            rebuild = True
            i += 1
        elif arg == "--issue":
            if i + 1 >= len(args):
                print("ralph: --issue requires an argument", file=sys.stderr)
                sys.exit(2)
            issue_number = args[i + 1]
            i += 2
        elif arg == "--poll":
            poll = True
            i += 1
        elif arg == "--interval":
            if i + 1 >= len(args):
                print("ralph: --interval requires an argument", file=sys.stderr)
                sys.exit(2)
            try:
                interval = parse_duration(args[i + 1])
            except ValueError as e:
                print(f"ralph: invalid duration: {e}", file=sys.stderr)
                sys.exit(2)
            i += 2
        elif arg == "--model":
            if i + 1 >= len(args):
                print("ralph: --model requires an argument", file=sys.stderr)
                sys.exit(2)
            model = args[i + 1]
            i += 2
        elif arg == "--timeout":
            if i + 1 >= len(args):
                print("ralph: --timeout requires an argument", file=sys.stderr)
                sys.exit(2)
            try:
                timeout_val = parse_duration(args[i + 1])
            except ValueError as e:
                print(f"ralph: invalid duration: {e}", file=sys.stderr)
                sys.exit(2)
            i += 2
        elif arg == "--agent":
            if i + 1 >= len(args):
                print("ralph: --agent requires an argument", file=sys.stderr)
                sys.exit(2)
            agent = args[i + 1]
            i += 2
        elif arg == "--auth":
            if i + 1 >= len(args):
                print("ralph: --auth requires an argument", file=sys.stderr)
                sys.exit(2)
            auth_mode = _parse_auth_mode(args[i + 1])
            i += 2
        elif arg in ("-h", "--help"):
            usage(0)
        elif arg.startswith("-"):
            print(f"ralph: unknown option: {arg}", file=sys.stderr)
            usage(1)
        else:
            print(f"ralph: unknown option: {arg}", file=sys.stderr)
            usage(1)
    # Resolve default model from agent config if not explicitly set
    agent_config = get_agent(agent)
    if model is None:
        model = agent_config["default_model"]

    # Validation
    if poll and issue_number:
        print("ralph: --poll and --issue cannot be used together", file=sys.stderr)
        sys.exit(2)

    if not poll and interval != 30:
        print("ralph: --interval requires --poll", file=sys.stderr)
        sys.exit(2)

    if timeout_val > 0 and not poll:
        print("ralph: --timeout requires --poll", file=sys.stderr)
        sys.exit(2)

    if not poll and not issue_number:
        print("ralph: no mode specified. Use --issue <number> or --poll", file=sys.stderr)
        usage(2)

    # Prerequisite checks
    check_dependencies_prereq()

    # Auth — ensure valid token exists before starting proxy
    # (auto-runs claude setup-token if missing/expired)
    token, token_data = ensure_token(agent, auth_mode)

    # Start proxy for agents that need it (e.g. claude).
    # Non-proxy agents (e.g. cursor) inject credentials via secret file.
    if agent_config["uses_proxy"]:
        proxy_port = proxy_port_for_agent(agent)
        ensure_proxy(agent, proxy_port, DOTFILES_DIR, auth_mode)
        start_proxy_keepalive(proxy_port)
    else:
        proxy_port = None

    # Git user config
    git = Git()
    git_user = git.output("config", "user.name") or "ralph"
    git_email = git.output("config", "user.email") or "ralph@localhost"
    gh = GitHub()

    # Issue mode
    if issue_number:
        rc = process_issue(int(issue_number), git, DOTFILES_DIR, gh, agent,
                           push, model, git_user, git_email, proxy_port,
                           token, rebuild=rebuild, auth_mode=auth_mode,
                           token_data=token_data)
        sys.exit(rc)

    # Poll mode
    if poll:
        poll_loop(git, DOTFILES_DIR, gh, agent, push, model, git_user,
                  git_email, proxy_port, token, interval, timeout_val,
                  rebuild=rebuild, auth_mode=auth_mode, token_data=token_data)
        sys.exit(0)
