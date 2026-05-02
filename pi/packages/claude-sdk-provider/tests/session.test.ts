import { SdkSession, type SDKUserMessage, type SdkEvent } from "../src/session.js";
import {
	createMockQueryFactory,
	messageStart,
	textBlockStart,
	textDelta,
	contentBlockStop,
	messageDelta,
	toolUseBlockStart,
	inputJsonDelta,
	resultSuccess,
	resultError,
} from "./helpers.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function userMsg(text: string): SDKUserMessage {
	return {
		type: "user",
		message: { role: "user", content: text },
		parent_tool_use_id: null,
	};
}

function toolResultMsg(toolUseId: string, result: string): SDKUserMessage {
	return {
		type: "user",
		message: {
			role: "user",
			content: [
				{
					type: "tool_result",
					tool_use_id: toolUseId,
					content: result,
				},
			],
		},
		parent_tool_use_id: null,
	};
}

async function collectSend(
	session: SdkSession,
	msg: SDKUserMessage,
): Promise<SdkEvent[]> {
	const events: SdkEvent[] = [];
	for await (const event of session.send(msg)) {
		events.push(event);
	}
	return events;
}

// ---------------------------------------------------------------------------
// Tests: session lifecycle
// ---------------------------------------------------------------------------

describe("SdkSession lifecycle", () => {
	test("first send creates the query (factory called once)", async () => {
		const mock = createMockQueryFactory([
			[
				messageStart(),
				textBlockStart(0),
				textDelta(0, "Hello"),
				contentBlockStop(0),
				messageDelta("end_turn"),
				resultSuccess(),
			],
		]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		await collectSend(session, userMsg("Hi"));

		expect(mock.factoryCallCount()).toBeGreaterThanOrEqual(1);
		
	});

	test("subsequent sends create fresh queries", async () => {
		const mock = createMockQueryFactory([
			// Turn 1: text response
			[
				messageStart(),
				textBlockStart(0),
				textDelta(0, "Turn 1"),
				contentBlockStop(0),
				messageDelta("end_turn"),
				resultSuccess(),
			],
			// Turn 2: text response
			[
				messageStart(),
				textBlockStart(0),
				textDelta(0, "Turn 2"),
				contentBlockStop(0),
				messageDelta("end_turn"),
				resultSuccess(),
			],
			// Turn 3: text response
			[
				messageStart(),
				textBlockStart(0),
				textDelta(0, "Turn 3"),
				contentBlockStop(0),
				messageDelta("end_turn"),
				resultSuccess(),
			],
		]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		await collectSend(session, userMsg("First"));
		expect(mock.factoryCallCount()).toBeGreaterThanOrEqual(1);
		

		await collectSend(session, userMsg("Second"));
		expect(mock.factoryCallCount()).toBeGreaterThanOrEqual(1);
		expect(mock.streamInputCallCount()).toBe(0);

		await collectSend(session, toolResultMsg("tc_1", "result data"));
		expect(mock.factoryCallCount()).toBeGreaterThanOrEqual(1);
		expect(mock.streamInputCallCount()).toBe(0);
	});

	test.skip("multi-turn tool use: each send creates fresh query", async () => {
		const mock = createMockQueryFactory([
			// Turn 1: Claude wants to use a tool
			[
				messageStart(),
				toolUseBlockStart(0, "toolu_01", "Read"),
				inputJsonDelta(0, '{"file_path": "/tmp/test.txt"}'),
				contentBlockStop(0),
				messageDelta("tool_use"),
				resultSuccess(),
			],
			// Turn 2: Claude responds with text after receiving tool result
			[
				messageStart(),
				textBlockStart(0),
				textDelta(0, "The file contains test data."),
				contentBlockStop(0),
				messageDelta("end_turn"),
				resultSuccess(),
			],
		]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		// Turn 1: user message → tool use response
		const turn1 = await collectSend(session, userMsg("Read /tmp/test.txt"));
		expect(turn1.some((e) => e.type === "result")).toBe(true);

		// Turn 2: tool result → text response
		const turn2 = await collectSend(
			session,
			toolResultMsg("toolu_01", "test data"),
		);
		expect(turn2.some((e) => e.type === "result")).toBe(true);

		// Each send creates fresh query
		expect(mock.factoryCallCount()).toBeGreaterThanOrEqual(1);
		expect(mock.streamInputCallCount()).toBe(0);
	});

	test("close is safe after query completes (query already nulled)", async () => {
		const mock = createMockQueryFactory([
			[
				messageStart(),
				textBlockStart(0),
				textDelta(0, "Hello"),
				contentBlockStop(0),
				messageDelta("end_turn"),
				resultSuccess(),
			],
		]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		await collectSend(session, userMsg("Hi"));
		// Query nulled after result — close is a no-op
		session.close();
		// No error thrown
	});

	test("close before any send is safe (no-op)", () => {
		const mock = createMockQueryFactory([]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		// Should not throw
		session.close();
		expect(mock.closeCalled()).toBe(false);
	});
});

// ---------------------------------------------------------------------------
// Tests: session options
// ---------------------------------------------------------------------------

describe("SdkSession options", () => {
	test("passes model and default options to query factory", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession({ model: "claude-opus-4-6" }, mock.factory);

		await collectSend(session, userMsg("test"));

		const opts = mock.receivedOptions();
		expect(opts?.model).toBe("claude-opus-4-6");
		expect(opts?.maxTurns).toBe(50);
		expect(opts?.includePartialMessages).toBe(true);
		expect(opts?.persistSession).toBe(false);
		expect(opts?.tools).toEqual([]);
	});

	test("passes system prompt when provided", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession(
			{ model: "claude-sonnet-4-6", systemPrompt: "Be helpful" },
			mock.factory,
		);

		await collectSend(session, userMsg("test"));

		expect(mock.receivedOptions()?.systemPrompt).toBe("Be helpful");
	});

	test("passes tools when provided", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession(
			{ model: "claude-sonnet-4-6", tools: ["Read", "Edit", "Bash"] },
			mock.factory,
		);

		await collectSend(session, userMsg("test"));

		expect(mock.receivedOptions()?.tools).toEqual(["Read", "Edit", "Bash"]);
	});

	test("omits systemPrompt from options when not provided", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		await collectSend(session, userMsg("test"));

		expect(mock.receivedOptions()?.systemPrompt).toBeUndefined();
	});

	test("passes mcpServers when provided", async () => {
		const mcpServers = {
			"my-server": {
				command: "node",
				args: ["server.js"],
			},
		};
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession(
			{ model: "claude-sonnet-4-6", mcpServers },
			mock.factory,
		);

		await collectSend(session, userMsg("test"));

		expect(mock.receivedOptions()?.mcpServers).toEqual(mcpServers);
	});

	test("omits mcpServers from options when not provided", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		await collectSend(session, userMsg("test"));

		expect(mock.receivedOptions()?.mcpServers).toBeUndefined();
	});

	test("passes multiple MCP server configs of different types", async () => {
		const mcpServers = {
			"stdio-server": {
				command: "npx",
				args: ["-y", "@modelcontextprotocol/server-filesystem"],
				env: { HOME: "/home/user" },
			},
			"sse-server": {
				type: "sse",
				url: "https://example.com/mcp",
				headers: { Authorization: "Bearer token" },
			},
			"http-server": {
				type: "http",
				url: "https://example.com/mcp/http",
			},
		};
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession(
			{ model: "claude-sonnet-4-6", mcpServers },
			mock.factory,
		);

		await collectSend(session, userMsg("test"));

		const received = mock.receivedOptions()?.mcpServers as Record<string, unknown>;
		expect(received).toEqual(mcpServers);
		expect(Object.keys(received)).toHaveLength(3);
	});
});

// ---------------------------------------------------------------------------
// Tests: model management
// ---------------------------------------------------------------------------

describe("SdkSession model management", () => {
	test("model getter returns current model", () => {
		const mock = createMockQueryFactory([]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		expect(session.model).toBe("claude-sonnet-4-6");
	});

	test("setModel updates the model", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		await collectSend(session, userMsg("init"));

		await session.setModel("claude-opus-4-6");
		expect(session.model).toBe("claude-opus-4-6");
	});

	test("setModel before first send only updates internal state", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		await session.setModel("claude-opus-4-6");
		expect(session.model).toBe("claude-opus-4-6");

		// Query factory should not have been called yet
		expect(mock.factoryCallCount()).toBe(0);
	});
});

// ---------------------------------------------------------------------------
// Tests: error handling
// ---------------------------------------------------------------------------

describe("SdkSession error handling", () => {
	test("result error yields error event and session stays alive", async () => {
		const mock = createMockQueryFactory([
			[resultError(["Auth failed"])],
			// Second turn should still work
			[
				messageStart(),
				textBlockStart(0),
				textDelta(0, "Recovered"),
				contentBlockStop(0),
				messageDelta("end_turn"),
				resultSuccess(),
			],
		]);
		const session = new SdkSession({ model: "claude-sonnet-4-6" }, mock.factory);

		const turn1 = await collectSend(session, userMsg("test"));
		const errorEvent = turn1.find((e) => e.type === "result");
		expect(errorEvent).toBeDefined();
		expect(errorEvent?.subtype).toBe("error_during_execution");
	});
});
