"""Agent configuration registry for ralph.

Centralizes per-agent differences (CLI commands, flags, network policy,
proxy usage, etc.) so the rest of the codebase can dispatch on agent name
without scattering if/else blocks.
"""


def _claude_cli_flags(model):
    """Return CLI flags for a Claude Code iteration."""
    return [
        "--dangerously-skip-permissions",
        "--effort", "high",
    ]


def _cursor_cli_flags(model):
    """Return CLI flags for a cursor-agent iteration."""
    return [
        "--force",
        "--trust",
        "--output-format", "text",
    ]


AGENTS = {
    "claude": {
        "cli_command": "claude",
        "sandbox_agent": "claude",
        "cli_flags": _claude_cli_flags,
        "allowed_hosts": [
            "api.anthropic.com",
            "statsig.anthropic.com",
            "sentry.io",
        ],
        "default_model": "sonnet",
        "uses_proxy": True,
        "env_var_name": "CLAUDE_CODE_OAUTH_TOKEN",
        "default_auth_mode": "oauth",
        "auth_modes": {
            "oauth": {
                "keychain_service": "claude-token",
                "validation_env_var": "CLAUDE_CODE_OAUTH_TOKEN",
            },
            "api_key": {
                "keychain_service": "claude-api-key",
                "validation_env_var": "ANTHROPIC_API_KEY",
            },
            "gateway": {
                "keychain_service": "claude-gateway",
                "validation_env_var": "ANTHROPIC_AUTH_TOKEN",
            },
        },
    },
    "cursor": {
        "cli_command": "cursor-agent",
        "sandbox_agent": "shell",
        "cli_flags": _cursor_cli_flags,
        "allowed_hosts": [
            "*.cursor.sh",
            "sentry.io",
        ],
        "default_model": "auto",
        "uses_proxy": False,
        "env_var_name": "CURSOR_API_KEY",
    },
}

VALID_AGENTS = list(AGENTS.keys())
VALID_AUTH_MODES = ["oauth", "api_key", "gateway"]


def get_agent(name):
    """Look up agent configuration by name.

    Returns a dict with keys: cli_command, sandbox_agent, cli_flags,
    allowed_hosts, default_model, uses_proxy, env_var_name.

    Raises ValueError for unknown agent names.
    """
    if name not in AGENTS:
        raise ValueError(
            f"ralph: unknown agent {name!r}"
            f" (expected one of: {', '.join(VALID_AGENTS)})")
    return AGENTS[name]


def get_auth_mode(agent, mode=None):
    """Return auth-mode config for an agent.

    - If the agent has no ``auth_modes`` entry, returns None regardless of mode.
    - If mode is None, uses the agent's ``default_auth_mode``.
    - Normalizes CLI-style "api-key" to internal "api_key".
    - Raises ValueError for unknown modes.
    """
    cfg = get_agent(agent)
    if "auth_modes" not in cfg:
        return None
    if mode is not None:
        mode = mode.replace("-", "_")
    else:
        mode = cfg["default_auth_mode"]
    if mode not in cfg["auth_modes"]:
        raise ValueError(
            f"ralph: unknown auth mode {mode!r} for agent {agent!r}")
    return cfg["auth_modes"][mode]
