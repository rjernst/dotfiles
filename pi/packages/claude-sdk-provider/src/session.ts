/**
 * Persistent SDK session manager.
 *
 * Wraps a Claude Agent SDK Query that persists across turns. The subprocess
 * starts once on first use and stays alive, receiving messages via
 * streamInput(). Combined with maxTurns: 1, each turn returns to pi for
 * tool execution without respawning the subprocess.
 */

// Type-only imports from the SDK — erased at compile time, so the SDK
// module is NOT eagerly loaded. The runtime import is deferred to first use
// via `await import(...)` in getQueryFactory().
import type {
	Options as SdkOptions,
	SDKUserMessage as SdkSDKUserMessage,
} from "@anthropic-ai/claude-agent-sdk";

// ---------------------------------------------------------------------------
// SDK-compatible types — minimal shapes for mocking/testing without
// requiring the full SDK process to be available.
// ---------------------------------------------------------------------------

/** Any SDK event — we only process specific subtypes at runtime. */
export interface SdkEvent {
	type: string;
	subtype?: string;
	[key: string]: unknown;
}

/** Anthropic MessageParam — minimal shape for SDK user messages. */
export interface MessageParam {
	role: "user" | "assistant";
	content: string | ContentBlock[];
}

/** Generic content block in a MessageParam. */
export interface ContentBlock {
	type: string;
	[key: string]: unknown;
}

/**
 * SDK user message — matches @anthropic-ai/claude-agent-sdk SDKUserMessage.
 * Used to send messages into a persistent session.
 */
export interface SDKUserMessage {
	type: "user";
	message: MessageParam;
	parent_tool_use_id: string | null;
}

/**
 * Minimal Query interface for dependency injection and testing.
 * Matches the subset of the SDK Query we actually use.
 */
export interface SdkQuery {
	next(): Promise<IteratorResult<SdkEvent>>;
	close(): void;
	streamInput(stream: AsyncIterable<SDKUserMessage>): Promise<void>;
	setModel(model?: string): Promise<void>;
	interrupt(): Promise<void>;
}

/**
 * Factory function that creates a Query from prompt + options.
 * In production, wraps the SDK's query(). In tests, returns a mock.
 */
export type QueryFactory = (params: {
	prompt: string | AsyncIterable<SDKUserMessage>;
	options?: SdkOptions;
}) => SdkQuery;

// ---------------------------------------------------------------------------
// Session options
// ---------------------------------------------------------------------------

/** Configuration for a persistent SDK session. */
export interface SdkSessionOptions {
	model: string;
	systemPrompt?: string;
	tools?: string[];
	maxTurns?: number;
	persistSession?: boolean;
	includePartialMessages?: boolean;
	mcpServers?: Record<string, Record<string, unknown>>;
}

// ---------------------------------------------------------------------------
// Session class
// ---------------------------------------------------------------------------

/**
 * Manages a persistent Claude Agent SDK subprocess session.
 *
 * The session starts the subprocess on first use and keeps it alive across
 * turns. Each turn sends an SDKUserMessage and yields response events until
 * a result is received. The subprocess maintains conversation history
 * internally — callers send only new messages each turn.
 */
export class SdkSession {
	private query: SdkQuery | null = null;
	private resolvedFactory: QueryFactory | null;
	private readonly options: SdkSessionOptions;
	private _model: string;

	constructor(options: SdkSessionOptions, queryFactory?: QueryFactory) {
		this.options = options;
		this._model = options.model;
		this.resolvedFactory = queryFactory ?? null;
	}

	/** Current model ID. */
	get model(): string {
		return this._model;
	}

	/**
	 * Send a user message and yield response events.
	 *
	 * On first call, starts the SDK subprocess via query(). On subsequent
	 * calls, feeds the message via streamInput(). Yields SDK events until
	 * a result is received, then returns without closing the query.
	 */
	async *send(msg: SDKUserMessage): AsyncGenerator<SdkEvent> {
		if (!this.query) {
			const factory = await this.getQueryFactory();
			this.query = factory({
				prompt: singleItemIterable(msg),
				options: this.buildSdkOptions(),
			});
		} else {
			await this.query.streamInput(singleItemIterable(msg));
		}

		// Read events until result. Uses manual .next() instead of for-await
		// to avoid generator cleanup closing the underlying query.
		while (true) {
			const { value, done } = await this.query.next();
			if (done) {
				// Process exited — null query so next send() starts fresh
				this.query = null;
				return;
			}
			yield value;
			if (value.type === "result") {
				// Turn complete — null query since subprocess may exit
				this.query = null;
				return;
			}
		}
	}

	/** Shut down the subprocess. */
	close(): void {
		this.query?.close();
		this.query = null;
	}

	/** Change model for subsequent turns. */
	async setModel(model: string): Promise<void> {
		this._model = model;
		if (this.query) {
			await this.query.setModel(model);
		}
	}

	/** Interrupt the current turn. */
	async interrupt(): Promise<void> {
		if (this.query) {
			await this.query.interrupt();
		}
	}

	/** Resolve the query factory — lazily imports the SDK on first use. */
	private async getQueryFactory(): Promise<QueryFactory> {
		if (!this.resolvedFactory) {
			const sdk = await import("@anthropic-ai/claude-agent-sdk");
			this.resolvedFactory = (params) => {
				// Our local SDKUserMessage is structurally compatible with
				// the SDK's type at runtime, but differs in the content
				// block union. Cast the prompt iterable specifically rather
				// than the whole function.
				const q = sdk.query({
					prompt: params.prompt as
						| string
						| AsyncIterable<SdkSDKUserMessage>,
					options: params.options,
				});
				// Narrow SDK's full Query (20+ methods) to the subset we use.
				return q as unknown as SdkQuery;
			};
		}
		return this.resolvedFactory;
	}

	/** Build SDK options from session configuration. */
	private buildSdkOptions(): SdkOptions {
		const opts: SdkOptions = {
			model: this._model,
			// High maxTurns: the SDK handles the full tool execution loop.
			// Each tool call + response is one turn.
			maxTurns: this.options.maxTurns ?? 50,
			includePartialMessages: this.options.includePartialMessages ?? true,
			persistSession: this.options.persistSession ?? false,
			tools: this.options.tools ?? [],
			// Bypass permissions — pi controls tool access, not the SDK
			permissionMode: 'bypassPermissions' as const,
		};
		if (this.options.systemPrompt) {
			opts.systemPrompt = this.options.systemPrompt;
		}
		if (this.options.mcpServers) {
			opts.mcpServers = this.options.mcpServers as SdkOptions["mcpServers"];
		}
		return opts;
	}
}

/** Create a single-item async iterable that yields one value then ends. */
async function* singleItemIterable<T>(item: T): AsyncGenerator<T> {
	yield item;
}
