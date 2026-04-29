/**
 * Claude SDK provider registration and auth guard.
 *
 * Registers provider id "claude-sdk" with model ids that route through
 * the Claude Agent SDK for subscription-backed usage. The provider is
 * subscription-first and fail-closed: if Claude SDK subscription auth is
 * unavailable, it errors clearly and never silently falls back to
 * ANTHROPIC_API_KEY or any other metered API path.
 *
 * Manages a persistent SDK session: the subprocess starts on first use
 * and stays alive across turns to avoid per-turn process boot overhead.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import {
	type Api,
	type AssistantMessage,
	type AssistantMessageEventStream,
	type Context,
	createAssistantMessageEventStream,
	type Model,
	type SimpleStreamOptions,
} from "@mariozechner/pi-ai";
import { createSdkStream } from "./stream.js";
import { SdkSession } from "./session.js";
import { extractSystemPrompt } from "./context.js";
import { getActiveClaudeTools } from "./tools.js";

/** Custom API identifier — not a real Anthropic API type. */
export const CLAUDE_SDK_API = "claude-sdk-stream" as Api;

/** Provider id registered with pi. */
export const PROVIDER_ID = "claude-sdk";

/**
 * Error message surfaced when subscription auth is unavailable.
 * Deliberately verbose so the user knows what to do.
 */
export const AUTH_ERROR_MESSAGE =
	"Claude SDK subscription auth is required but unavailable. " +
	"This provider uses the Claude Agent SDK which requires an active Claude " +
	"subscription (Pro/Max) authenticated via Claude Code. " +
	"Run `claude auth login` to authenticate with your subscription. " +
	"This provider intentionally does not fall back to ANTHROPIC_API_KEY or " +
	"any other metered API billing path.";

/**
 * Minimal model configuration — the variable parts that differ per model.
 * Loaded from models.json at runtime so models can be updated without
 * recompiling TypeScript.
 */
export interface ModelConfig {
	id: string;
	name: string;
	contextWindow?: number;
	maxTokens?: number;
}

/**
 * Build full pi model definitions from minimal configs.
 * Adds the constant fields that are the same for all subscription-backed
 * models: zero cost, reasoning enabled, text+image input.
 */
export function buildModelDefs(configs: ModelConfig[]) {
	return configs.map((c) => ({
		id: c.id,
		name: c.name,
		reasoning: true as const,
		input: ["text", "image"] as ("text" | "image")[],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: c.contextWindow ?? 200000,
		maxTokens: c.maxTokens ?? 64000,
	}));
}

// ---------------------------------------------------------------------------
// Session lifecycle
// ---------------------------------------------------------------------------

let activeSession: SdkSession | null = null;
let messagesSent = 0;

/**
 * Reset session state. Exported for testing only — production code should
 * not call this directly.
 */
export function _resetSession(): void {
	if (activeSession) {
		activeSession.close();
	}
	activeSession = null;
	messagesSent = 0;
}

/**
 * Set MCP server config. Exported for testing only — production code
 * sets this via registerClaudeSdkProvider.
 */
export function _setMcpServers(config: McpServersConfig | undefined): void {
	mcpServersConfig = config;
}

// Clean up on process exit
process.on("exit", () => {
	activeSession?.close();
});

// ---------------------------------------------------------------------------
// Streaming adapter
// ---------------------------------------------------------------------------

/**
 * Streaming adapter that routes through the Claude Agent SDK.
 *
 * Auth guard: rejects if an API key is provided (subscription-only).
 * Then delegates to createSdkStream via a persistent session.
 */
export function streamClaudeSdk(
	model: Model<Api>,
	context: Context,
	options?: SimpleStreamOptions,
): AssistantMessageEventStream {
	// Auth guard: reject if an API key was passed instead of SDK auth.
	// The Claude SDK provider must never use ANTHROPIC_API_KEY or any
	// per-token billing path. The apiKey field in SimpleStreamOptions is
	// populated from the provider's apiKey config; for this provider we
	// intentionally leave that unset and rely on the SDK's own auth.
	if (options?.apiKey) {
		const stream = createAssistantMessageEventStream();
		(async () => {
			const output: AssistantMessage = {
				role: "assistant",
				content: [],
				api: model.api,
				provider: model.provider,
				model: model.id,
				usage: {
					input: 0,
					output: 0,
					cacheRead: 0,
					cacheWrite: 0,
					totalTokens: 0,
					cost: {
						input: 0,
						output: 0,
						cacheRead: 0,
						cacheWrite: 0,
						total: 0,
					},
				},
				stopReason: "error",
				errorMessage:
					"claude-sdk provider received an API key, but this provider " +
					"is subscription-only. Do not set ANTHROPIC_API_KEY or any " +
					"apiKey for this provider. " +
					AUTH_ERROR_MESSAGE,
				timestamp: Date.now(),
			};
			stream.push({
				type: "error",
				reason: "error",
				error: output,
			});
			stream.end();
		})();
		return stream;
	}

	// Detect new conversation (message count decreased) or model change
	if (activeSession) {
		if (
			context.messages.length < messagesSent ||
			activeSession.model !== model.id
		) {
			activeSession.close();
			activeSession = null;
			messagesSent = 0;
		}
	}

	// Create session on first use
	if (!activeSession) {
		activeSession = new SdkSession({
			model: model.id,
			systemPrompt: extractSystemPrompt(context),
			tools: getActiveClaudeTools(context.tools),
			mcpServers: mcpServersConfig,
		});
		messagesSent = 0;
	}

	// Extract new messages (skip what SDK already has)
	const newMessages = context.messages.slice(messagesSent);
	messagesSent = context.messages.length;

	return createSdkStream(model, options, activeSession, newMessages);
}

/**
 * MCP server configuration passed through to the Claude Agent SDK.
 * Loaded from mcp-servers.json at the package root. When set, the SDK
 * subprocess connects to these MCP servers and their tools become
 * available to Claude alongside the built-in pi tools.
 */
export type McpServersConfig = Record<string, Record<string, unknown>>;

/** Module-level MCP server config, set at registration time. */
let mcpServersConfig: McpServersConfig | undefined;

/**
 * Registers the claude-sdk provider with pi.
 *
 * The provider is registered with:
 * - No apiKey (subscription auth only — never falls back to ANTHROPIC_API_KEY)
 * - A custom streamSimple that guards against API key usage and routes
 *   through the Claude Agent SDK
 * - Model definitions built from models.json with zero cost (subscription-billed)
 * - Optional MCP server configs forwarded to the SDK subprocess
 */
export function registerClaudeSdkProvider(
	pi: ExtensionAPI,
	models: ReturnType<typeof buildModelDefs>,
	mcpServers?: McpServersConfig,
): void {
	mcpServersConfig = mcpServers;
	pi.registerProvider(PROVIDER_ID, {
		// No baseUrl — SDK manages its own endpoint
		// No apiKey — subscription auth only, enforced by streamSimple
		api: CLAUDE_SDK_API,
		models,
		streamSimple: streamClaudeSdk,
	});
}
