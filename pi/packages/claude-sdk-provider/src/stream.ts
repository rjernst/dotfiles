/**
 * Claude SDK streaming adapter.
 *
 * Executes queries through a persistent SdkSession and converts SDK stream
 * events into pi assistant message events.
 */

import type {
	Api,
	AssistantMessage,
	AssistantMessageEventStream,
	Message,
	Model,
	SimpleStreamOptions,
	StopReason,
	TextContent,
	ThinkingContent,
	ToolCall,
} from "@mariozechner/pi-ai";
import { createAssistantMessageEventStream } from "@mariozechner/pi-ai";
import { buildUserMessage } from "./context.js";
import { mapClaudeArgsToPi, toPiName } from "./tools.js";
import type { SdkEvent, SdkSession } from "./session.js";

// Re-export SdkEvent for consumers that need the type
export type { SdkEvent } from "./session.js";

// ---------------------------------------------------------------------------
// Stop reason mapping
// ---------------------------------------------------------------------------

/** Map Anthropic stop reason to pi StopReason. */
export function mapStopReason(reason: string | null | undefined): StopReason {
	switch (reason) {
		case "end_turn":
		case "pause_turn":
		case "stop_sequence":
			return "stop";
		case "max_tokens":
			return "length";
		case "tool_use":
			return "toolUse";
		default:
			return "stop";
	}
}

// ---------------------------------------------------------------------------
// Block tracking
// ---------------------------------------------------------------------------

/** Maps SDK content block indices to pi output array indices. */
interface BlockTracker {
	type: "text" | "thinking" | "toolCall";
	sdkIndex: number;
	piIndex: number;
	partialJson?: string;
	/** Original Claude tool name, used for argument mapping. */
	claudeToolName?: string;
}

// ---------------------------------------------------------------------------
// Output template
// ---------------------------------------------------------------------------

/** Create a fresh AssistantMessage template for the stream output. */
export function makeOutputTemplate(model: Model<Api>): AssistantMessage {
	return {
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
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 },
		},
		stopReason: "stop",
		timestamp: Date.now(),
	};
}

// ---------------------------------------------------------------------------
// Main entry point
// ---------------------------------------------------------------------------

/**
 * Create a pi AssistantMessageEventStream backed by a persistent SdkSession.
 *
 * @param model       - pi model definition
 * @param options     - pi stream options (signal, maxTokens, reasoning, etc.)
 * @param session     - persistent SDK session (started on first use)
 * @param newMessages - messages not yet sent to the SDK subprocess
 */
export function createSdkStream(
	model: Model<Api>,
	options: SimpleStreamOptions | undefined,
	session: SdkSession,
	newMessages: Message[],
): AssistantMessageEventStream {
	const stream = createAssistantMessageEventStream();

	(async () => {
		const output = makeOutputTemplate(model);

		try {
			const userMessage = buildUserMessage(newMessages);

			stream.push({ type: "start", partial: output });

			const blocks: BlockTracker[] = [];

			for await (const msg of session.send(userMessage)) {
				if (options?.signal?.aborted) {
					throw new Error("Request was aborted");
				}

				if (msg.type === "stream_event") {
					const event = msg.event as Record<string, unknown>;
					if (event) {
						processStreamEvent(event, output, stream, blocks);
					}
				} else if (msg.type === "result") {
					if (
						typeof msg.subtype === "string" &&
						msg.subtype.startsWith("error")
					) {
						const errors = (msg.errors as string[]) || [];
						throw new Error(
							errors.length > 0
								? errors.join("; ")
								: "Claude SDK query failed",
						);
					}
					// Success — break the loop
					break;
				}
			}

			stream.push({
				type: "done",
				reason: output.stopReason as "stop" | "length" | "toolUse",
				message: output,
			});
			stream.end();
		} catch (error) {
			output.stopReason = options?.signal?.aborted ? "aborted" : "error";
			output.errorMessage =
				error instanceof Error ? error.message : String(error);
			stream.push({
				type: "error",
				reason: output.stopReason,
				error: output,
			});
			stream.end();
		}
	})();

	return stream;
}

// ---------------------------------------------------------------------------
// Stream event processing
// ---------------------------------------------------------------------------

/**
 * Process a single BetaRawMessageStreamEvent from the SDK.
 *
 * Converts Anthropic-format streaming events into pi assistant message
 * events and updates the output AssistantMessage in place.
 */
function processStreamEvent(
	event: Record<string, unknown>,
	output: AssistantMessage,
	stream: AssistantMessageEventStream,
	blocks: BlockTracker[],
): void {
	const eventType = event.type as string;

	if (eventType === "message_start") {
		const message = event.message as Record<string, unknown> | undefined;
		if (message?.usage) {
			updateUsage(output, message.usage as Record<string, number>);
		}
	} else if (eventType === "content_block_start") {
		handleContentBlockStart(event, output, stream, blocks);
	} else if (eventType === "content_block_delta") {
		handleContentBlockDelta(event, output, stream, blocks);
	} else if (eventType === "content_block_stop") {
		handleContentBlockStop(event, output, stream, blocks);
	} else if (eventType === "message_delta") {
		const delta = event.delta as Record<string, unknown> | undefined;
		if (delta?.stop_reason) {
			output.stopReason = mapStopReason(delta.stop_reason as string);
		}
		if (event.usage) {
			updateUsage(output, event.usage as Record<string, number>);
		}
	}
}

function handleContentBlockStart(
	event: Record<string, unknown>,
	output: AssistantMessage,
	stream: AssistantMessageEventStream,
	blocks: BlockTracker[],
): void {
	const contentBlock = event.content_block as Record<string, unknown>;
	const sdkIndex = event.index as number;

	if (contentBlock.type === "text") {
		output.content.push({ type: "text", text: "" } as TextContent);
		const piIndex = output.content.length - 1;
		blocks.push({ type: "text", sdkIndex, piIndex });
		stream.push({
			type: "text_start",
			contentIndex: piIndex,
			partial: output,
		});
	} else if (contentBlock.type === "thinking") {
		output.content.push({
			type: "thinking",
			thinking: "",
		} as ThinkingContent);
		const piIndex = output.content.length - 1;
		blocks.push({ type: "thinking", sdkIndex, piIndex });
		stream.push({
			type: "thinking_start",
			contentIndex: piIndex,
			partial: output,
		});
	} else if (contentBlock.type === "tool_use") {
		const claudeName = contentBlock.name as string;
		const piName = toPiName(claudeName) ?? claudeName;
		const toolCall: ToolCall = {
			type: "toolCall",
			id: contentBlock.id as string,
			name: piName,
			arguments: {},
		};
		output.content.push(toolCall);
		const piIndex = output.content.length - 1;
		blocks.push({
			type: "toolCall",
			sdkIndex,
			piIndex,
			partialJson: "",
			claudeToolName: claudeName,
		});
		stream.push({
			type: "toolcall_start",
			contentIndex: piIndex,
			partial: output,
		});
	}
}

function handleContentBlockDelta(
	event: Record<string, unknown>,
	output: AssistantMessage,
	stream: AssistantMessageEventStream,
	blocks: BlockTracker[],
): void {
	const sdkIndex = event.index as number;
	const tracker = blocks.find((b) => b.sdkIndex === sdkIndex);
	if (!tracker) return;

	const delta = event.delta as Record<string, unknown>;
	const piIndex = tracker.piIndex;

	if (delta.type === "text_delta" && tracker.type === "text") {
		const text = delta.text as string;
		(output.content[piIndex] as TextContent).text += text;
		stream.push({
			type: "text_delta",
			contentIndex: piIndex,
			delta: text,
			partial: output,
		});
	} else if (delta.type === "thinking_delta" && tracker.type === "thinking") {
		const thinking = delta.thinking as string;
		(output.content[piIndex] as ThinkingContent).thinking += thinking;
		stream.push({
			type: "thinking_delta",
			contentIndex: piIndex,
			delta: thinking,
			partial: output,
		});
	} else if (
		delta.type === "input_json_delta" &&
		tracker.type === "toolCall"
	) {
		const partialJson = delta.partial_json as string;
		tracker.partialJson = (tracker.partialJson || "") + partialJson;
		// Tool arguments are JSON objects/arrays. Only attempt a parse when
		// the accumulated string could be complete (ends with } or ]).
		// This avoids throwing + catching on every intermediate delta.
		const last = tracker.partialJson[tracker.partialJson.length - 1];
		if (last === "}" || last === "]") {
			try {
				let args = JSON.parse(tracker.partialJson);
				if (tracker.claudeToolName) {
					args = mapClaudeArgsToPi(tracker.claudeToolName, args);
				}
				(output.content[piIndex] as ToolCall).arguments = args;
			} catch {
				// Looks complete but isn't (e.g. nested braces) — keep accumulating
			}
		}
		stream.push({
			type: "toolcall_delta",
			contentIndex: piIndex,
			delta: partialJson,
			partial: output,
		});
	} else if (
		delta.type === "signature_delta" &&
		tracker.type === "thinking"
	) {
		const signature = (delta as Record<string, unknown>).signature as string;
		const block = output.content[piIndex] as ThinkingContent;
		block.thinkingSignature = (block.thinkingSignature || "") + signature;
	}
}

function handleContentBlockStop(
	event: Record<string, unknown>,
	output: AssistantMessage,
	stream: AssistantMessageEventStream,
	blocks: BlockTracker[],
): void {
	const sdkIndex = event.index as number;
	const tracker = blocks.find((b) => b.sdkIndex === sdkIndex);
	if (!tracker) return;

	const piIndex = tracker.piIndex;

	if (tracker.type === "text") {
		const block = output.content[piIndex] as TextContent;
		stream.push({
			type: "text_end",
			contentIndex: piIndex,
			content: block.text,
			partial: output,
		});
	} else if (tracker.type === "thinking") {
		const block = output.content[piIndex] as ThinkingContent;
		stream.push({
			type: "thinking_end",
			contentIndex: piIndex,
			content: block.thinking,
			partial: output,
		});
	} else if (tracker.type === "toolCall") {
		// Final JSON parse attempt with argument mapping
		if (tracker.partialJson) {
			try {
				let args = JSON.parse(tracker.partialJson);
				if (tracker.claudeToolName) {
					args = mapClaudeArgsToPi(tracker.claudeToolName, args);
				}
				(output.content[piIndex] as ToolCall).arguments = args;
			} catch {
				// Keep whatever was last successfully parsed
			}
		}
		const block = output.content[piIndex] as ToolCall;
		stream.push({
			type: "toolcall_end",
			contentIndex: piIndex,
			toolCall: block,
			partial: output,
		});
	}
}

// ---------------------------------------------------------------------------
// Usage tracking
// ---------------------------------------------------------------------------

/** Update output usage from SDK/Anthropic usage data. */
function updateUsage(
	output: AssistantMessage,
	usage: Record<string, number>,
): void {
	output.usage.input = usage.input_tokens ?? output.usage.input;
	output.usage.output = usage.output_tokens ?? output.usage.output;
	output.usage.cacheRead =
		usage.cache_read_input_tokens ?? output.usage.cacheRead;
	output.usage.cacheWrite =
		usage.cache_creation_input_tokens ?? output.usage.cacheWrite;
	output.usage.totalTokens =
		output.usage.input +
		output.usage.output +
		output.usage.cacheRead +
		output.usage.cacheWrite;
	// Cost stays 0 for subscription usage
}
