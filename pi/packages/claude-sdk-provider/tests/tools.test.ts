import {
	toClaudeName,
	toPiName,
	mapClaudeArgsToPi,
	getActiveClaudeTools,
} from "../src/tools.js";
import { createSdkStream } from "../src/stream.js";
import { buildUserMessage } from "../src/context.js";
import type { AssistantMessageEvent, Context } from "@mariozechner/pi-ai";
import { CLAUDE_SDK_API, PROVIDER_ID } from "../src/provider.js";
import {
	makeModel,
	makeTool,
	collectEvents,
	createTestSession,
	createMockQueryFactory,
	makeUserMessage,
	messageStart,
	toolUseBlockStart,
	inputJsonDelta,
	contentBlockStop,
	messageDelta,
	resultSuccess,
} from "./helpers.js";
import { SdkSession } from "../src/session.js";

// ---------------------------------------------------------------------------
// Tests: tool name mapping
// ---------------------------------------------------------------------------

describe("tool name mapping: pi → Claude", () => {
	test("read → Read", () => expect(toClaudeName("read")).toBe("Read"));
	test("write → Write", () => expect(toClaudeName("write")).toBe("Write"));
	test("edit → Edit", () => expect(toClaudeName("edit")).toBe("Edit"));
	test("bash → Bash", () => expect(toClaudeName("bash")).toBe("Bash"));
	test("grep → Grep", () => expect(toClaudeName("grep")).toBe("Grep"));
	test("find → Glob", () => expect(toClaudeName("find")).toBe("Glob"));
	test("unknown → undefined", () =>
		expect(toClaudeName("unknown")).toBeUndefined());
});

describe("tool name mapping: Claude → pi", () => {
	test("Read → read", () => expect(toPiName("Read")).toBe("read"));
	test("Write → write", () => expect(toPiName("Write")).toBe("write"));
	test("Edit → edit", () => expect(toPiName("Edit")).toBe("edit"));
	test("Bash → bash", () => expect(toPiName("Bash")).toBe("bash"));
	test("Grep → grep", () => expect(toPiName("Grep")).toBe("grep"));
	test("Glob → find", () => expect(toPiName("Glob")).toBe("find"));
	test("Unknown → undefined", () =>
		expect(toPiName("Unknown")).toBeUndefined());
});

// ---------------------------------------------------------------------------
// Tests: argument mapping
// ---------------------------------------------------------------------------

describe("argument mapping: Claude → pi", () => {
	test("Read: file_path → path, preserves offset and limit", () => {
		const result = mapClaudeArgsToPi("Read", {
			file_path: "/tmp/test.txt",
			offset: 10,
			limit: 100,
		});
		expect(result).toEqual({ path: "/tmp/test.txt", offset: 10, limit: 100 });
	});

	test("Write: file_path → path, preserves content", () => {
		const result = mapClaudeArgsToPi("Write", {
			file_path: "/tmp/out.txt",
			content: "hello",
		});
		expect(result).toEqual({ path: "/tmp/out.txt", content: "hello" });
	});

	test("Edit: file_path → path, old_string → oldText, new_string → newText", () => {
		const result = mapClaudeArgsToPi("Edit", {
			file_path: "/tmp/file.ts",
			old_string: "foo",
			new_string: "bar",
		});
		expect(result).toEqual({
			path: "/tmp/file.ts",
			oldText: "foo",
			newText: "bar",
		});
	});

	test("Edit: extra parameters like replace_all pass through unchanged", () => {
		const result = mapClaudeArgsToPi("Edit", {
			file_path: "/tmp/file.ts",
			old_string: "a",
			new_string: "b",
			replace_all: true,
		});
		expect(result).toEqual({
			path: "/tmp/file.ts",
			oldText: "a",
			newText: "b",
			replace_all: true,
		});
	});

	test("Bash: command and timeout pass through unchanged", () => {
		const result = mapClaudeArgsToPi("Bash", {
			command: "ls -la",
			timeout: 5000,
		});
		expect(result).toEqual({ command: "ls -la", timeout: 5000 });
	});

	test("Grep: head_limit → limit, preserves pattern/path/glob", () => {
		const result = mapClaudeArgsToPi("Grep", {
			pattern: "TODO",
			path: "/src",
			glob: "*.ts",
			head_limit: 50,
		});
		expect(result).toEqual({
			pattern: "TODO",
			path: "/src",
			glob: "*.ts",
			limit: 50,
		});
	});

	test("Glob: pattern and path pass through unchanged", () => {
		const result = mapClaudeArgsToPi("Glob", {
			pattern: "**/*.ts",
			path: "/src",
		});
		expect(result).toEqual({ pattern: "**/*.ts", path: "/src" });
	});

	test("unknown tool preserves all arguments", () => {
		const result = mapClaudeArgsToPi("CustomTool", {
			foo: "bar",
			baz: 42,
		});
		expect(result).toEqual({ foo: "bar", baz: 42 });
	});

	test("empty arguments produce empty result", () => {
		const result = mapClaudeArgsToPi("Read", {});
		expect(result).toEqual({});
	});
});

// ---------------------------------------------------------------------------
// Tests: active tool filtering
// ---------------------------------------------------------------------------

describe("getActiveClaudeTools", () => {
	test("maps active pi tools to Claude names", () => {
		const tools = [makeTool("read"), makeTool("write"), makeTool("bash")];
		expect(getActiveClaudeTools(tools)).toEqual(["Read", "Write", "Bash"]);
	});

	test("omits pi tools without a known mapping", () => {
		const tools = [makeTool("read"), makeTool("custom_tool")];
		expect(getActiveClaudeTools(tools)).toEqual(["Read"]);
	});

	test("returns empty array for undefined tools", () => {
		expect(getActiveClaudeTools(undefined)).toEqual([]);
	});

	test("returns empty array for empty tools list", () => {
		expect(getActiveClaudeTools([])).toEqual([]);
	});

	test("includes all six built-in mappings when all are active", () => {
		const tools = [
			makeTool("read"),
			makeTool("write"),
			makeTool("edit"),
			makeTool("bash"),
			makeTool("grep"),
			makeTool("find"),
		];
		expect(getActiveClaudeTools(tools)).toEqual([
			"Read",
			"Write",
			"Edit",
			"Bash",
			"Grep",
			"Glob",
		]);
	});

	test("inactive tools are not exposed (only subset active)", () => {
		const tools = [makeTool("read"), makeTool("bash")];
		const result = getActiveClaudeTools(tools);
		expect(result).toEqual(["Read", "Bash"]);
		expect(result).not.toContain("Edit");
		expect(result).not.toContain("Write");
		expect(result).not.toContain("Grep");
		expect(result).not.toContain("Glob");
	});
});

// ---------------------------------------------------------------------------
// Tests: SDK-side execution denial
// ---------------------------------------------------------------------------

describe("SDK-side execution denial", () => {
	test("maxTurns is set to 1, preventing SDK tool execution loops", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession(
			{
				model: "claude-sonnet-4-6",
				tools: ["Read", "Bash"],
			},
			mock.factory,
		);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			session,
			[makeUserMessage("test")],
		);
		await collectEvents(stream);

		expect(mock.receivedOptions()?.maxTurns).toBe(1);
	});

	test("active tools are passed to SDK as Claude names", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession(
			{
				model: "claude-sonnet-4-6",
				tools: ["Read", "Edit", "Bash"],
			},
			mock.factory,
		);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			session,
			[makeUserMessage("test")],
		);
		await collectEvents(stream);

		expect(mock.receivedOptions()?.tools).toEqual(["Read", "Edit", "Bash"]);
	});

	test("context without tools passes empty tool list to SDK", async () => {
		const mock = createMockQueryFactory([[resultSuccess()]]);
		const session = new SdkSession(
			{ model: "claude-sonnet-4-6" },
			mock.factory,
		);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			session,
			[],
		);
		await collectEvents(stream);

		expect(mock.receivedOptions()?.tools).toEqual([]);
	});
});

// ---------------------------------------------------------------------------
// Tests: tool call integration with stream
// ---------------------------------------------------------------------------

describe("tool call mapping in stream", () => {
	test("Claude Edit tool call is translated to pi edit with mapped args", async () => {
		const session = createTestSession([
			messageStart(50),
			toolUseBlockStart(0, "toolu_edit", "Edit"),
			inputJsonDelta(
				0,
				'{"file_path": "/src/app.ts", "old_string": "foo", "new_string": "bar"}',
			),
			contentBlockStop(0),
			messageDelta("tool_use", 15),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			session,
			[makeUserMessage("test")],
		);
		const events = await collectEvents(stream);

		const toolEnd = events.find((e) => e.type === "toolcall_end") as Extract<
			AssistantMessageEvent,
			{ type: "toolcall_end" }
		>;
		expect(toolEnd.toolCall.name).toBe("edit");
		expect(toolEnd.toolCall.arguments).toEqual({
			path: "/src/app.ts",
			oldText: "foo",
			newText: "bar",
		});
	});

	test("Claude Read tool call maps file_path to path", async () => {
		const session = createTestSession([
			messageStart(50),
			toolUseBlockStart(0, "toolu_read", "Read"),
			inputJsonDelta(
				0,
				'{"file_path": "/tmp/test.txt", "offset": 0, "limit": 50}',
			),
			contentBlockStop(0),
			messageDelta("tool_use", 10),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			session,
			[makeUserMessage("test")],
		);
		const events = await collectEvents(stream);

		const toolEnd = events.find((e) => e.type === "toolcall_end") as Extract<
			AssistantMessageEvent,
			{ type: "toolcall_end" }
		>;
		expect(toolEnd.toolCall.name).toBe("read");
		expect(toolEnd.toolCall.arguments).toEqual({
			path: "/tmp/test.txt",
			offset: 0,
			limit: 50,
		});
	});

	test("Claude Grep tool call maps head_limit to limit", async () => {
		const session = createTestSession([
			messageStart(50),
			toolUseBlockStart(0, "toolu_grep", "Grep"),
			inputJsonDelta(
				0,
				'{"pattern": "TODO", "path": "/src", "head_limit": 100}',
			),
			contentBlockStop(0),
			messageDelta("tool_use", 10),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			session,
			[makeUserMessage("test")],
		);
		const events = await collectEvents(stream);

		const toolEnd = events.find((e) => e.type === "toolcall_end") as Extract<
			AssistantMessageEvent,
			{ type: "toolcall_end" }
		>;
		expect(toolEnd.toolCall.name).toBe("grep");
		expect(toolEnd.toolCall.arguments).toEqual({
			pattern: "TODO",
			path: "/src",
			limit: 100,
		});
	});

	test("Claude Glob tool call maps name to find", async () => {
		const session = createTestSession([
			messageStart(50),
			toolUseBlockStart(0, "toolu_glob", "Glob"),
			inputJsonDelta(0, '{"pattern": "**/*.ts", "path": "/src"}'),
			contentBlockStop(0),
			messageDelta("tool_use", 10),
			resultSuccess(),
		]);

		const stream = createSdkStream(
			makeModel(),
			undefined,
			session,
			[makeUserMessage("test")],
		);
		const events = await collectEvents(stream);

		const toolEnd = events.find((e) => e.type === "toolcall_end") as Extract<
			AssistantMessageEvent,
			{ type: "toolcall_end" }
		>;
		expect(toolEnd.toolCall.name).toBe("find");
		expect(toolEnd.toolCall.arguments).toEqual({
			pattern: "**/*.ts",
			path: "/src",
		});
	});

	test("tool results from pi are preserved in follow-up context", () => {
		const messages = [
			{
				role: "user" as const,
				content: "Read a file",
				timestamp: Date.now(),
			},
			{
				role: "assistant" as const,
				content: [
					{
						type: "toolCall" as const,
						id: "tc_1",
						name: "read",
						arguments: { path: "/tmp/test.txt" },
					},
				],
				api: CLAUDE_SDK_API,
				provider: PROVIDER_ID,
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
				stopReason: "toolUse" as const,
				timestamp: Date.now(),
			},
			{
				role: "toolResult" as const,
				toolCallId: "tc_1",
				toolName: "read",
				content: [{ type: "text" as const, text: "file contents here" }],
				isError: false,
				timestamp: Date.now(),
			},
			{
				role: "user" as const,
				content: "Now write something",
				timestamp: Date.now(),
			},
		];

		const userMessage = buildUserMessage(messages);
		// Assistant messages are filtered out; tool result + user text remain
		const content = userMessage.message.content as Array<
			Record<string, unknown>
		>;
		expect(content.some((c) => c.type === "tool_result")).toBe(true);
		expect(
			content.some(
				(c) => c.type === "tool_result" && c.tool_use_id === "tc_1",
			),
		).toBe(true);
		expect(
			content.some(
				(c) => c.type === "text" && c.text === "Now write something",
			),
		).toBe(true);
	});
});
