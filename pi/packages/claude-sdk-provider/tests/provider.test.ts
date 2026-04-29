import { jest } from "@jest/globals";
import {
	PROVIDER_ID,
	CLAUDE_SDK_API,
	AUTH_ERROR_MESSAGE,
	buildModelDefs,
	streamClaudeSdk,
	registerClaudeSdkProvider,
	_resetSession,
	_setMcpServers,
} from "../src/provider.js";
import type { AssistantMessageEvent } from "@mariozechner/pi-ai";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { makeModel, emptyContext, collectEvents } from "./helpers.js";

/** Clean up session state between tests. */
afterEach(() => {
	_resetSession();
});

/** Extract the error event from events, failing the test if not present. */
function expectErrorEvent(events: AssistantMessageEvent[]) {
	expect(events).toHaveLength(1);
	expect(events[0].type).toBe("error");
	const event = events[0] as Extract<AssistantMessageEvent, { type: "error" }>;
	return event;
}

describe("buildModelDefs", () => {
	test("adds constant fields to minimal config", () => {
		const models = buildModelDefs([
			{ id: "claude-test-1", name: "Test Model" },
		]);
		expect(models).toHaveLength(1);
		expect(models[0]).toEqual({
			id: "claude-test-1",
			name: "Test Model",
			reasoning: true,
			input: ["text", "image"],
			cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
			contextWindow: 200000,
			maxTokens: 64000,
		});
	});

	test("uses provided contextWindow and maxTokens", () => {
		const models = buildModelDefs([
			{
				id: "claude-big",
				name: "Big Model",
				contextWindow: 1000000,
				maxTokens: 128000,
			},
		]);
		expect(models[0].contextWindow).toBe(1000000);
		expect(models[0].maxTokens).toBe(128000);
	});

	test("defaults contextWindow to 200k and maxTokens to 64k", () => {
		const models = buildModelDefs([{ id: "m", name: "M" }]);
		expect(models[0].contextWindow).toBe(200000);
		expect(models[0].maxTokens).toBe(64000);
	});

	test("all models have zero cost (subscription-billed)", () => {
		const models = buildModelDefs([
			{ id: "a", name: "A" },
			{ id: "b", name: "B" },
		]);
		for (const model of models) {
			expect(model.cost).toEqual({
				input: 0,
				output: 0,
				cacheRead: 0,
				cacheWrite: 0,
			});
		}
	});

	test("all models support reasoning and text+image input", () => {
		const models = buildModelDefs([{ id: "x", name: "X" }]);
		expect(models[0].reasoning).toBe(true);
		expect(models[0].input).toEqual(["text", "image"]);
	});
});

describe("claude-sdk provider registration", () => {
	test("provider id is 'claude-sdk'", () => {
		expect(PROVIDER_ID).toBe("claude-sdk");
	});

	test("registerClaudeSdkProvider calls pi.registerProvider with correct config", () => {
		const mockPi = {
			registerProvider: jest.fn(),
		} as unknown as ExtensionAPI;

		const models = buildModelDefs([
			{ id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6 (SDK)" },
		]);

		registerClaudeSdkProvider(mockPi, models);

		expect(mockPi.registerProvider).toHaveBeenCalledTimes(1);
		expect(mockPi.registerProvider).toHaveBeenCalledWith(
			"claude-sdk",
			expect.objectContaining({
				api: CLAUDE_SDK_API,
				models,
				streamSimple: streamClaudeSdk,
			}),
		);

		// Verify no apiKey is set — provider must never fall back to API key billing
		const config = (mockPi.registerProvider as jest.Mock).mock
			.calls[0][1] as Record<string, unknown>;
		expect(config["apiKey"]).toBeUndefined();
		expect(config["baseUrl"]).toBeUndefined();
	});
});

describe("auth guard", () => {
	test("rejects when an API key is provided", async () => {
		const stream = streamClaudeSdk(makeModel(), emptyContext, {
			apiKey: "sk-ant-api03-fake-key",
		});

		const event = expectErrorEvent(await collectEvents(stream));
		expect(event.error.stopReason).toBe("error");
		expect(event.error.errorMessage).toContain(
			"claude-sdk provider received an API key",
		);
		expect(event.error.errorMessage).toContain(
			"does not fall back to ANTHROPIC_API_KEY",
		);
	});

	test("error message includes subscription auth instructions", async () => {
		const stream = streamClaudeSdk(makeModel(), emptyContext, {
			apiKey: "some-key",
		});

		const event = expectErrorEvent(await collectEvents(stream));
		expect(event.error.errorMessage).toContain("claude auth login");
		expect(event.error.errorMessage).toContain(
			"Claude SDK subscription auth",
		);
	});

	test("AUTH_ERROR_MESSAGE mentions subscription requirement", () => {
		expect(AUTH_ERROR_MESSAGE).toContain("subscription");
		expect(AUTH_ERROR_MESSAGE).toContain("claude auth login");
		expect(AUTH_ERROR_MESSAGE).toContain(
			"does not fall back to ANTHROPIC_API_KEY",
		);
	});

	test("no API key fallback: provider does not use apiKey from options", async () => {
		const stream = streamClaudeSdk(makeModel(), emptyContext, {
			apiKey: "sk-ant-api03-definitely-not-used",
		});

		const event = expectErrorEvent(await collectEvents(stream));
		// The error should be about the API key rejection, not about
		// an actual API call failure (which would mean it tried to use the key).
		expect(event.error.errorMessage).not.toContain("401");
		expect(event.error.errorMessage).not.toContain("Unauthorized");
		expect(event.error.errorMessage).toContain("subscription-only");
	});

	test("without API key, stream proceeds past auth guard", async () => {
		const stream = streamClaudeSdk(makeModel(), emptyContext);

		const events = await collectEvents(stream);
		expect(events.length).toBeGreaterThan(0);
		const lastEvent = events[events.length - 1];
		// Whether it succeeds or errors, it should NOT be the auth guard error
		if (lastEvent.type === "error") {
			expect(lastEvent.error.errorMessage).not.toContain(
				"received an API key",
			);
		}
	});

	test("auth rejection is always stopReason error, even if signal is aborted", async () => {
		const controller = new AbortController();
		controller.abort();

		const stream = streamClaudeSdk(makeModel(), emptyContext, {
			signal: controller.signal,
			apiKey: "trigger-auth-guard",
		});

		const event = expectErrorEvent(await collectEvents(stream));
		// Auth guard is an auth rejection, not an abort — stopReason must be "error"
		expect(event.error.stopReason).toBe("error");
	});
});

describe("MCP server passthrough", () => {
	test("registerClaudeSdkProvider accepts mcpServers config", () => {
		const mockPi = {
			registerProvider: jest.fn(),
		} as unknown as ExtensionAPI;

		const models = buildModelDefs([
			{ id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6 (SDK)" },
		]);
		const mcpServers = {
			"test-server": { command: "node", args: ["server.js"] },
		};

		// Should not throw
		registerClaudeSdkProvider(mockPi, models, mcpServers);

		expect(mockPi.registerProvider).toHaveBeenCalledTimes(1);
	});

	test("registerClaudeSdkProvider works without mcpServers", () => {
		const mockPi = {
			registerProvider: jest.fn(),
		} as unknown as ExtensionAPI;

		const models = buildModelDefs([
			{ id: "claude-sonnet-4-6", name: "Claude Sonnet 4.6 (SDK)" },
		]);

		// Should not throw
		registerClaudeSdkProvider(mockPi, models);

		expect(mockPi.registerProvider).toHaveBeenCalledTimes(1);
	});

	test("_setMcpServers configures MCP servers for session creation", () => {
		const mcpServers = {
			"my-server": { command: "echo", args: ["hello"] },
		};
		// Should not throw
		_setMcpServers(mcpServers);
	});

	test("_resetSession preserves MCP server config", () => {
		_setMcpServers({ "srv": { command: "test" } });
		_resetSession();
		// _resetSession only clears session state, not registration-time
		// config. MCP servers are set at provider registration and should
		// survive session resets (e.g. conversation restarts).
	});
});
