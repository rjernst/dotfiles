/**
 * Claude SDK provider registration and auth guard.
 *
 * Registers provider id "claude-sdk" with model ids that route through
 * the Claude Agent SDK for subscription-backed usage. The provider is
 * subscription-first and fail-closed: if Claude SDK subscription auth is
 * unavailable, it errors clearly and never silently falls back to
 * ANTHROPIC_API_KEY or any other metered API path.
 *
 * Architecture: the SDK subprocess runs the full agent loop. Pi's tools
 * are registered as MCP tools so the SDK executes them in-process. Pi
 * renders the streaming events and provides configuration (system prompt,
 * tool definitions, etc.).
 */

import { createCodingTools } from "@mariozechner/pi-coding-agent";
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
import { createPiMcpServer } from "./mcp-tools.js";

/** Custom API identifier — not a real Anthropic API type. */
export const CLAUDE_SDK_API = "claude-sdk-stream" as Api;

/** Provider id registered with pi. */
export const PROVIDER_ID = "claude-sdk";

/** Sentinel apiKey — satisfies pi validation, never sent over the wire. */
export const SENTINEL_API_KEY = "claude-sdk-subscription";

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

/**
 * Reset session state. Exported for testing only — production code should
 * not call this directly.
 */
export function _resetSession(): void {
	if (activeSession) {
		activeSession.close();
	}
	activeSession = null;
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
 * The SDK runs the full agent loop: Claude proposes tool calls, the SDK
 * executes them via our MCP handlers (which use pi's tool implementations),
 * and Claude responds with the final result. Pi renders all streaming events.
 */
export function streamClaudeSdk(
	model: Model<Api>,
	context: Context,
	options?: SimpleStreamOptions,
): AssistantMessageEventStream {
	// Auth guard: reject if a real API key was passed.
	if (options?.apiKey && options.apiKey !== SENTINEL_API_KEY) {
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
			stream.push({ type: "error", reason: "error", error: output });
			stream.end();
		})();
		return stream;
	}

	// Detect model change — need a fresh session
	if (activeSession && activeSession.model !== model.id) {
		activeSession.close();
		activeSession = null;
	}

	// Always send full context — each turn starts a fresh subprocess
	const newMessages = context.messages;

	// The stream handles async session setup (MCP server creation) internally.
	return createSdkStream(model, options, newMessages, () =>
		getOrCreateSession(model.id, context),
	);
}

/**
 * Get or create the active SDK session.
 *
 * On first call, creates pi tool instances, wraps them in an MCP server,
 * and starts the session with tools: [] (builtins disabled) plus the MCP
 * server (pi tools enabled).
 */
async function getOrCreateSession(
	modelId: string,
	context: Context,
): Promise<SdkSession> {
	if (activeSession) return activeSession;

	// Create pi tool instances for the current working directory
	const cwd = process.cwd();
	const piTools = createCodingTools(cwd);

	// Wrap pi's tools in an MCP server
	const { server } = await createPiMcpServer(piTools);

	// Merge with any user-configured MCP servers
	const mcpServers: Record<string, Record<string, unknown>> = {
		...(mcpServersConfig ?? {}),
		[server.name ?? "pi_tools"]: server as unknown as Record<
			string,
			unknown
		>,
	};

	activeSession = new SdkSession({
		model: modelId,
		systemPrompt: extractSystemPrompt(context),
		// Disable all Claude Code builtins — only MCP tools are available
		tools: [],
		mcpServers,
	});

	return activeSession;
}

/**
 * MCP server configuration passed through to the Claude Agent SDK.
 */
export type McpServersConfig = Record<string, Record<string, unknown>>;

/** Module-level MCP server config, set at registration time. */
let mcpServersConfig: McpServersConfig | undefined;

/**
 * Registers the claude-sdk provider with pi.
 */
export function registerClaudeSdkProvider(
	pi: ExtensionAPI,
	models: ReturnType<typeof buildModelDefs>,
	mcpServers?: McpServersConfig,
): void {
	mcpServersConfig = mcpServers;
	pi.registerProvider(PROVIDER_ID, {
		baseUrl: "https://sdk.internal.unused",
		apiKey: SENTINEL_API_KEY,
		api: CLAUDE_SDK_API,
		models,
		streamSimple: streamClaudeSdk,
	});
}
