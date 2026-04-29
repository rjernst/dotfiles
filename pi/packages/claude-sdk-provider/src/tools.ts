/**
 * Built-in tool mapping between Claude SDK and pi.
 *
 * Claude uses PascalCase tool names with Anthropic-style parameter naming.
 * Pi uses lowercase tool names with its own parameter naming.
 *
 * This module maps between the two so Claude can propose tool calls that
 * pi can execute, and active pi tools can be exposed to the SDK.
 */

import type { Tool } from "@mariozechner/pi-ai";

// ---------------------------------------------------------------------------
// Tool name mapping
// ---------------------------------------------------------------------------

/** Map from pi built-in tool name to Claude SDK tool name. */
const PI_TO_CLAUDE: Record<string, string> = {
	read: "Read",
	write: "Write",
	edit: "Edit",
	bash: "Bash",
	grep: "Grep",
	find: "Glob",
};

/** Map from Claude SDK tool name to pi built-in tool name. */
const CLAUDE_TO_PI: Record<string, string> = {};
for (const [pi, claude] of Object.entries(PI_TO_CLAUDE)) {
	CLAUDE_TO_PI[claude] = pi;
}

/** Convert a pi tool name to the corresponding Claude SDK tool name. */
export function toClaudeName(piName: string): string | undefined {
	return PI_TO_CLAUDE[piName];
}

/** Convert a Claude SDK tool name to the corresponding pi tool name. */
export function toPiName(claudeName: string): string | undefined {
	return CLAUDE_TO_PI[claudeName];
}

// ---------------------------------------------------------------------------
// Argument mapping (canonical: pi → Claude, reverse generated)
// ---------------------------------------------------------------------------

/**
 * Canonical parameter name mapping from pi to Claude, keyed by Claude
 * tool name. Only parameters that differ need to be listed — parameters
 * with the same name in both systems (e.g. offset, command, pattern, path,
 * content, timeout, glob) pass through unchanged.
 */
const PI_TO_CLAUDE_ARGS: Record<string, Record<string, string>> = {
	Read: { path: "file_path" },
	Write: { path: "file_path" },
	Edit: { path: "file_path", oldText: "old_string", newText: "new_string" },
	// Bash: command, timeout — same names in both systems
	Grep: { limit: "head_limit" },
	// Glob: pattern, path — same names in both systems
};

/** Generated reverse: Claude arg name → pi arg name. */
const CLAUDE_TO_PI_ARGS: Record<string, Record<string, string>> = {};
for (const [tool, mapping] of Object.entries(PI_TO_CLAUDE_ARGS)) {
	CLAUDE_TO_PI_ARGS[tool] = {};
	for (const [piArg, claudeArg] of Object.entries(mapping)) {
		CLAUDE_TO_PI_ARGS[tool][claudeArg] = piArg;
	}
}

/**
 * Map Claude tool call arguments to pi parameter names.
 *
 * Known parameter names are mapped according to CLAUDE_TO_PI_ARGS.
 * Unknown/unmapped parameters are preserved as-is.
 */
export function mapClaudeArgsToPi(
	claudeToolName: string,
	args: Record<string, unknown>,
): Record<string, unknown> {
	const mapping = CLAUDE_TO_PI_ARGS[claudeToolName];
	if (!mapping) return { ...args };

	const result: Record<string, unknown> = {};
	for (const [key, value] of Object.entries(args)) {
		result[mapping[key] ?? key] = value;
	}
	return result;
}

// ---------------------------------------------------------------------------
// Active tool filtering
// ---------------------------------------------------------------------------

/**
 * Get the list of Claude SDK tool names to expose, based on active pi tools.
 *
 * Only returns Claude tool names that have a known built-in mapping AND
 * whose corresponding pi tool is in the provided (active) tool list.
 * Custom pi tools without a known mapping are silently omitted.
 */
export function getActiveClaudeTools(tools: Tool[] | undefined): string[] {
	if (!tools) return [];
	return tools
		.map((t) => PI_TO_CLAUDE[t.name])
		.filter((name): name is string => name !== undefined);
}

