/**
 * Pi context → Claude SDK message conversion.
 *
 * Converts pi's message-based context into SDKUserMessage objects suitable
 * for the persistent session's streaming input.
 */

import type { Context, Message, TextContent } from "@mariozechner/pi-ai";
import type { ContentBlock, SDKUserMessage } from "./session.js";

/**
 * Extract the system prompt from pi context for SDK systemPrompt option.
 */
export function extractSystemPrompt(context: Context): string | undefined {
	return context.systemPrompt || undefined;
}

/**
 * Build an SDKUserMessage from pi messages.
 *
 * Converts user messages and tool results into an Anthropic-format
 * MessageParam wrapped in an SDKUserMessage. Assistant messages are
 * skipped — the SDK subprocess already has them from its own responses.
 *
 * For a single user text message, the content is a plain string.
 * For tool results (or mixed content), the content is an array of
 * content blocks.
 */
export function buildUserMessage(messages: Message[]): SDKUserMessage {
	// Filter out assistant messages — SDK already has them
	const relevant = messages.filter((m) => m.role !== "assistant");

	if (relevant.length === 0) {
		return {
			type: "user",
			message: { role: "user", content: "" },
			parent_tool_use_id: null,
		};
	}

	// Single user message → simple text content
	if (relevant.length === 1 && relevant[0].role === "user") {
		return {
			type: "user",
			message: { role: "user", content: extractUserText(relevant[0]) },
			parent_tool_use_id: null,
		};
	}

	// Mixed content → array of content blocks
	const content: ContentBlock[] = [];

	for (const msg of relevant) {
		if (msg.role === "user") {
			const text = extractUserText(msg);
			if (text) {
				content.push({ type: "text", text });
			}
		} else if (msg.role === "toolResult") {
			const resultText = msg.content
				.filter((c): c is TextContent => c.type === "text")
				.map((c) => c.text)
				.join("\n");
			const block: ContentBlock = {
				type: "tool_result",
				tool_use_id: msg.toolCallId,
				content: resultText,
			};
			if (msg.isError) {
				block.is_error = true;
			}
			content.push(block);
		}
	}

	return {
		type: "user",
		message: { role: "user", content },
		parent_tool_use_id: null,
	};
}

/** Extract text content from a user message. */
function extractUserText(msg: Message): string {
	if (msg.role !== "user") return "";
	if (typeof msg.content === "string") {
		return msg.content;
	}
	return msg.content
		.filter((c): c is TextContent => c.type === "text")
		.map((c) => c.text)
		.join("\n");
}
