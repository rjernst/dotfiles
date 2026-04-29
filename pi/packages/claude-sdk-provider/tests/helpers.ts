/**
 * Shared test utilities for claude-sdk-provider tests.
 */

import type {
	Api,
	AssistantMessageEvent,
	Context,
	Model,
	Tool,
} from "@mariozechner/pi-ai";
import { CLAUDE_SDK_API, PROVIDER_ID } from "../src/provider.js";
import type { Options as SdkOptions } from "@anthropic-ai/claude-agent-sdk";
import {
	SdkSession,
	type SdkEvent,
	type SdkQuery,
	type SDKUserMessage,
	type QueryFactory,
} from "../src/session.js";

// Re-export SdkEvent for test files that need it
export type { SdkEvent } from "../src/session.js";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

export function makeModel(id = "claude-sonnet-4-6"): Model<Api> {
	return {
		id,
		name: `${id} (SDK)`,
		api: CLAUDE_SDK_API,
		provider: PROVIDER_ID,
		baseUrl: "",
		reasoning: true,
		input: ["text", "image"],
		cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
		contextWindow: 200000,
		maxTokens: 64000,
	};
}

export function makeTool(name: string): Tool {
	return {
		name,
		description: `${name} tool`,
		parameters: { type: "object", properties: {} } as any,
	};
}

export const emptyContext: Context = { messages: [] };

// ---------------------------------------------------------------------------
// Stream collection
// ---------------------------------------------------------------------------

export async function collectEvents(
	stream: AsyncIterable<AssistantMessageEvent>,
): Promise<AssistantMessageEvent[]> {
	const events: AssistantMessageEvent[] = [];
	for await (const event of stream) {
		events.push(event);
	}
	return events;
}

// ---------------------------------------------------------------------------
// Mock SDK query factory for session-based testing
// ---------------------------------------------------------------------------

export interface MockQueryFactoryResult {
	/** Factory to pass to SdkSession constructor. */
	factory: QueryFactory;
	/** Number of times the factory was called (should be 1 for persistent session). */
	factoryCallCount: () => number;
	/** Number of times streamInput was called (one per subsequent turn). */
	streamInputCallCount: () => number;
	/** Whether close() was called on the query. */
	closeCalled: () => boolean;
	/** SDK options received when the query was created. */
	receivedOptions: () => SdkOptions | undefined;
}

/**
 * Create a mock QueryFactory that returns a query yielding predefined
 * events for each turn.
 *
 * @param turns - Array of event arrays; turns[0] for the first send(),
 *                turns[1] for the second send() (via streamInput), etc.
 */
export function createMockQueryFactory(
	turns: SdkEvent[][],
): MockQueryFactoryResult {
	let turnIndex = 0;
	let eventIndex = 0;
	let _factoryCallCount = 0;
	let _streamInputCallCount = 0;
	let _closeCalled = false;
	let _receivedOptions: SdkOptions | undefined;

	const query: SdkQuery = {
		async next(): Promise<IteratorResult<SdkEvent>> {
			if (
				turnIndex >= turns.length ||
				eventIndex >= turns[turnIndex].length
			) {
				return { value: undefined as unknown as SdkEvent, done: true };
			}
			const value = turns[turnIndex][eventIndex++];
			return { value, done: false };
		},
		close() {
			_closeCalled = true;
		},
		async streamInput(_stream: AsyncIterable<SDKUserMessage>) {
			// Consume the stream to advance the iterable
			for await (const _msg of _stream) {
				/* discard */
			}
			_streamInputCallCount++;
			turnIndex++;
			eventIndex = 0;
		},
		async setModel() {},
		async interrupt() {},
	};

	const factory: QueryFactory = (params) => {
		_factoryCallCount++;
		_receivedOptions = params.options;
		return query;
	};

	return {
		factory,
		factoryCallCount: () => _factoryCallCount,
		streamInputCallCount: () => _streamInputCallCount,
		closeCalled: () => _closeCalled,
		receivedOptions: () => _receivedOptions,
	};
}

/**
 * Create a test SdkSession backed by a mock query factory.
 * Convenience wrapper for single-turn tests.
 */
export function createTestSession(
	events: SdkEvent[],
	model = "claude-sonnet-4-6",
): SdkSession {
	const { factory } = createMockQueryFactory([events]);
	return new SdkSession({ model }, factory);
}

/**
 * Create a user message fixture for tests.
 */
export function makeUserMessage(text: string) {
	return {
		role: "user" as const,
		content: text,
		timestamp: Date.now(),
	};
}

// ---------------------------------------------------------------------------
// SDK stream event factories — match BetaRawMessageStreamEvent shapes
// ---------------------------------------------------------------------------

export function messageStart(
	inputTokens = 10,
	outputTokens = 0,
): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "message_start",
			message: {
				usage: {
					input_tokens: inputTokens,
					output_tokens: outputTokens,
				},
			},
		},
	};
}

export function textBlockStart(index: number): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "content_block_start",
			index,
			content_block: { type: "text" },
		},
	};
}

export function textDelta(index: number, text: string): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "content_block_delta",
			index,
			delta: { type: "text_delta", text },
		},
	};
}

export function thinkingBlockStart(index: number): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "content_block_start",
			index,
			content_block: { type: "thinking" },
		},
	};
}

export function thinkingDelta(index: number, thinking: string): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "content_block_delta",
			index,
			delta: { type: "thinking_delta", thinking },
		},
	};
}

export function signatureDelta(index: number, signature: string): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "content_block_delta",
			index,
			delta: { type: "signature_delta", signature },
		},
	};
}

export function toolUseBlockStart(
	index: number,
	id: string,
	name: string,
): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "content_block_start",
			index,
			content_block: { type: "tool_use", id, name },
		},
	};
}

export function inputJsonDelta(index: number, partialJson: string): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "content_block_delta",
			index,
			delta: { type: "input_json_delta", partial_json: partialJson },
		},
	};
}

export function contentBlockStop(index: number): SdkEvent {
	return {
		type: "stream_event",
		event: { type: "content_block_stop", index },
	};
}

export function messageDelta(
	stopReason: string,
	outputTokens = 5,
): SdkEvent {
	return {
		type: "stream_event",
		event: {
			type: "message_delta",
			delta: { stop_reason: stopReason },
			usage: { output_tokens: outputTokens },
		},
	};
}

export function resultSuccess(): SdkEvent {
	return {
		type: "result",
		subtype: "success",
		result: "",
	};
}

export function resultError(errors: string[]): SdkEvent {
	return {
		type: "result",
		subtype: "error_during_execution",
		errors,
	};
}
