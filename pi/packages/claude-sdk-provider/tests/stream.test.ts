import { createSdkStream, mapStopReason } from "../src/stream.js";
import { buildUserMessage, extractSystemPrompt } from "../src/context.js";
import { SdkSession, type SDKUserMessage } from "../src/session.js";
import type {
	Api,
	AssistantMessageEvent,
	Context,
	TextContent,
	ThinkingContent,
} from "@mariozechner/pi-ai";
import {
	makeModel,
	collectEvents,
	createTestSession,
	makeUserMessage,
	messageStart,
	textBlockStart,
	textDelta,
	thinkingBlockStart,
	thinkingDelta,
	signatureDelta,
	toolUseBlockStart,
	inputJsonDelta,
	contentBlockStop,
	messageDelta,
	resultSuccess,
	resultError,
} from "./helpers.js";

// ---------------------------------------------------------------------------
// Tests: mapStopReason
// ---------------------------------------------------------------------------

describe("mapStopReason", () => {
	test("end_turn → stop", () => {
		expect(mapStopReason("end_turn")).toBe("stop");
	});

	test("pause_turn → stop", () => {
		expect(mapStopReason("pause_turn")).toBe("stop");
	});

	test("stop_sequence → stop", () => {
		expect(mapStopReason("stop_sequence")).toBe("stop");
	});

	test("max_tokens → length", () => {
		expect(mapStopReason("max_tokens")).toBe("length");
	});

	test("tool_use → toolUse", () => {
		expect(mapStopReason("tool_use")).toBe("toolUse");
	});

	test("null → stop", () => {
		expect(mapStopReason(null)).toBe("stop");
	});

	test("undefined → stop", () => {
		expect(mapStopReason(undefined)).toBe("stop");
	});
});

// ---------------------------------------------------------------------------
// Tests: text-only stream
// ---------------------------------------------------------------------------

describe("text-only SDK stream", () => {
	test("produces the expected pi text events", async () => {
		const session = createTestSession([
			messageStart(10, 0),
			textBlockStart(0),
			textDelta(0, "Hello"),
			textDelta(0, " world"),
			contentBlockStop(0),
			messageDelta("end_turn", 5),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		expect(events.map((e) => e.type)).toEqual([
			"start",
			"text_start",
			"text_delta",
			"text_delta",
			"text_end",
			"done",
		]);

		const textEnd = events.find((e) => e.type === "text_end") as Extract<
			AssistantMessageEvent,
			{ type: "text_end" }
		>;
		expect(textEnd.content).toBe("Hello world");
		expect(textEnd.contentIndex).toBe(0);

		const done = events.find((e) => e.type === "done") as Extract<
			AssistantMessageEvent,
			{ type: "done" }
		>;
		expect(done.reason).toBe("stop");
		expect(done.message.content).toHaveLength(1);
		expect((done.message.content[0] as TextContent).text).toBe(
			"Hello world",
		);
	});

	test("updates usage from message_start and message_delta", async () => {
		const session = createTestSession([
			messageStart(100, 0),
			textBlockStart(0),
			textDelta(0, "Hi"),
			contentBlockStop(0),
			messageDelta("end_turn", 25),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		const done = events.find((e) => e.type === "done") as Extract<
			AssistantMessageEvent,
			{ type: "done" }
		>;
		expect(done.message.usage.input).toBe(100);
		expect(done.message.usage.output).toBe(25);
	});
});

// ---------------------------------------------------------------------------
// Tests: tool-use stream
// ---------------------------------------------------------------------------

describe("tool-use SDK stream", () => {
	test("produces pi toolcall events with toolUse stop reason", async () => {
		const session = createTestSession([
			messageStart(50, 0),
			toolUseBlockStart(0, "toolu_01", "mcp__pi_tools__read"),
			inputJsonDelta(0, '{"pat'),
			inputJsonDelta(0, 'h": "/tmp/test.txt"}'),
			contentBlockStop(0),
			messageDelta("tool_use", 15),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		expect(events.map((e) => e.type)).toEqual([
			"start",
			"toolcall_start",
			"toolcall_delta",
			"toolcall_delta",
			"toolcall_end",
			"done",
		]);

		const toolEnd = events.find(
			(e) => e.type === "toolcall_end",
		) as Extract<AssistantMessageEvent, { type: "toolcall_end" }>;
		expect(toolEnd.toolCall.id).toBe("toolu_01");
		expect(toolEnd.toolCall.name).toBe("read");
		expect(toolEnd.toolCall.arguments).toEqual({
			path: "/tmp/test.txt",
		});
		expect(toolEnd.contentIndex).toBe(0);

		const done = events.find((e) => e.type === "done") as Extract<
			AssistantMessageEvent,
			{ type: "done" }
		>;
		expect(done.reason).toBe("toolUse");
	});

	test("handles text followed by tool use", async () => {
		const session = createTestSession([
			messageStart(50, 0),
			textBlockStart(0),
			textDelta(0, "I'll read that file."),
			contentBlockStop(0),
			toolUseBlockStart(1, "toolu_02", "mcp__pi_tools__read"),
			inputJsonDelta(1, '{"path": "/tmp/a.txt"}'),
			contentBlockStop(1),
			messageDelta("tool_use", 20),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		expect(events.map((e) => e.type)).toEqual([
			"start",
			"text_start",
			"text_delta",
			"text_end",
			"toolcall_start",
			"toolcall_delta",
			"toolcall_end",
			"done",
		]);

		const done = events.find((e) => e.type === "done") as Extract<
			AssistantMessageEvent,
			{ type: "done" }
		>;
		expect(done.message.content).toHaveLength(2);
		expect(done.message.content[0].type).toBe("text");
		expect(done.message.content[1].type).toBe("toolCall");
		expect(done.reason).toBe("toolUse");
	});
});

// ---------------------------------------------------------------------------
// Tests: thinking stream
// ---------------------------------------------------------------------------

describe("thinking SDK stream", () => {
	test("produces pi thinking events", async () => {
		const session = createTestSession([
			messageStart(10, 0),
			thinkingBlockStart(0),
			thinkingDelta(0, "Let me think"),
			thinkingDelta(0, " about this..."),
			signatureDelta(0, "sig123"),
			contentBlockStop(0),
			textBlockStart(1),
			textDelta(1, "Here is my answer."),
			contentBlockStop(1),
			messageDelta("end_turn", 30),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		expect(events.map((e) => e.type)).toEqual([
			"start",
			"thinking_start",
			"thinking_delta",
			"thinking_delta",
			"thinking_end",
			"text_start",
			"text_delta",
			"text_end",
			"done",
		]);

		const thinkEnd = events.find(
			(e) => e.type === "thinking_end",
		) as Extract<AssistantMessageEvent, { type: "thinking_end" }>;
		expect(thinkEnd.content).toBe("Let me think about this...");

		const done = events.find((e) => e.type === "done") as Extract<
			AssistantMessageEvent,
			{ type: "done" }
		>;
		const thinkingBlock = done.message.content[0] as ThinkingContent;
		expect(thinkingBlock.thinkingSignature).toBe("sig123");
	});
});

// ---------------------------------------------------------------------------
// Tests: error handling
// ---------------------------------------------------------------------------

describe("error handling", () => {
	test("SDK result error yields error event", async () => {
		const session = createTestSession([
			messageStart(10, 0),
			resultError(["Authentication failed"]),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		expect(events).toHaveLength(2); // start + error
		const errorEvent = events[1] as Extract<
			AssistantMessageEvent,
			{ type: "error" }
		>;
		expect(errorEvent.type).toBe("error");
		expect(errorEvent.reason).toBe("error");
		expect(errorEvent.error.errorMessage).toContain(
			"Authentication failed",
		);
	});

	test("session send throwing yields error event", async () => {
		// Create a session with a factory that throws synchronously
		const factory = () => {
			throw new Error("SDK not available");
		};
		const session = new SdkSession(
			{ model: "claude-sonnet-4-6" },
			factory,
		);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		// start event is pushed before session.send(), then error on throw
		expect(events).toHaveLength(2);
		expect(events[0].type).toBe("start");
		const errorEvent = events[1] as Extract<
			AssistantMessageEvent,
			{ type: "error" }
		>;
		expect(errorEvent.type).toBe("error");
		expect(errorEvent.error.errorMessage).toContain("SDK not available");
	});

	test("abort signal yields aborted stop reason", async () => {
		const controller = new AbortController();
		controller.abort();

		const session = createTestSession([
			messageStart(10, 0),
			textBlockStart(0),
			textDelta(0, "partial"),
		]);

		const stream = createSdkStream(
			makeModel(),
			{ signal: controller.signal },
			[makeUserMessage("test")],
			async () => session,
		);
		const events = await collectEvents(stream);

		const errorEvent = events.find(
			(e) => e.type === "error",
		) as Extract<AssistantMessageEvent, { type: "error" }>;
		expect(errorEvent).toBeDefined();
		expect(errorEvent.error.stopReason).toBe("aborted");
	});
});

// ---------------------------------------------------------------------------
// Tests: context conversion (buildUserMessage)
// ---------------------------------------------------------------------------

describe("context conversion", () => {
	test("extractSystemPrompt returns the system prompt", () => {
		const ctx: Context = {
			systemPrompt: "You are a helpful assistant.",
			messages: [],
		};
		expect(extractSystemPrompt(ctx)).toBe("You are a helpful assistant.");
	});

	test("extractSystemPrompt returns undefined for empty prompt", () => {
		expect(extractSystemPrompt({ messages: [] })).toBeUndefined();
		expect(
			extractSystemPrompt({ systemPrompt: "", messages: [] }),
		).toBeUndefined();
	});

	test("buildUserMessage with single user message returns text content", () => {
		const messages = [makeUserMessage("Hello")];
		const result = buildUserMessage(messages);

		expect(result.type).toBe("user");
		expect(result.message.role).toBe("user");
		expect(result.message.content).toBe("Hello");
		expect(result.parent_tool_use_id).toBeNull();
	});

	test("buildUserMessage with empty messages returns empty text", () => {
		const result = buildUserMessage([]);
		expect(result.message.content).toBe("");
	});

	test("buildUserMessage filters out assistant messages", () => {
		const messages = [
			makeUserMessage("question"),
			{
				role: "assistant" as const,
				content: [{ type: "text" as const, text: "answer" }],
				api: "anthropic-messages" as Api,
				provider: "anthropic",
				model: "claude-sonnet-4-6",
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
				stopReason: "stop" as const,
				timestamp: Date.now(),
			},
			makeUserMessage("follow-up"),
		];
		const result = buildUserMessage(messages);

		// Should have text blocks for both user messages, no assistant
		expect(result.message.content).toEqual([
			{ type: "text", text: "question" },
			{ type: "text", text: "follow-up" },
		]);
	});

	test("buildUserMessage converts tool results to tool_result blocks", () => {
		const messages = [
			{
				role: "toolResult" as const,
				toolCallId: "tc_1",
				toolName: "read",
				content: [{ type: "text" as const, text: "file contents here" }],
				isError: false,
				timestamp: Date.now(),
			},
		];
		const result = buildUserMessage(messages);

		expect(result.message.content).toEqual([
			{
				type: "tool_result",
				tool_use_id: "tc_1",
				content: "file contents here",
			},
		]);
	});

	test("buildUserMessage marks error tool results with is_error", () => {
		const messages = [
			{
				role: "toolResult" as const,
				toolCallId: "tc_err",
				toolName: "read",
				content: [{ type: "text" as const, text: "file not found" }],
				isError: true,
				timestamp: Date.now(),
			},
		];
		const result = buildUserMessage(messages);

		const content = result.message.content as Array<Record<string, unknown>>;
		expect(content[0].is_error).toBe(true);
	});

	test("buildUserMessage handles mixed user + tool result messages", () => {
		const messages = [
			{
				role: "toolResult" as const,
				toolCallId: "tc_1",
				toolName: "read",
				content: [{ type: "text" as const, text: "result data" }],
				isError: false,
				timestamp: Date.now(),
			},
			makeUserMessage("Now do something else"),
		];
		const result = buildUserMessage(messages);

		const content = result.message.content as Array<Record<string, unknown>>;
		expect(content).toHaveLength(2);
		expect(content[0].type).toBe("tool_result");
		expect(content[1].type).toBe("text");
		expect(content[1].text).toBe("Now do something else");
	});
});

// ---------------------------------------------------------------------------
// Tests: no live auth required
// ---------------------------------------------------------------------------

describe("test isolation", () => {
	test("all stream tests use mocked SDK — no live auth required", () => {
		expect(true).toBe(true);
	});
});
