/**
 * MCP tool bridge — executes pi's tools inside the Claude Agent SDK.
 *
 * Creates an in-process MCP server via createSdkMcpServer() with handlers
 * that call pi's actual tool implementations. The SDK calls these handlers
 * when Claude proposes tool use, keeping tool execution in pi's process
 * with pi's configuration.
 *
 * Tools are registered with lowercase names (read, bash, edit, write) to
 * distinguish them from Claude Code's PascalCase builtins. The SDK sees
 * them as MCP tools and routes calls through our handlers.
 */

import type { AgentTool } from "@mariozechner/pi-agent-core";
import type { McpSdkServerConfigWithInstance, SdkMcpToolDefinition } from "@anthropic-ai/claude-agent-sdk";

/** MCP tool name prefix: mcp__pi_tools__<name> */
export const MCP_SERVER_NAME = "pi_tools";

/** Extract the pi tool name from an MCP-qualified name. */
export function fromMcpName(mcpName: string): string | undefined {
	const prefix = `mcp__${MCP_SERVER_NAME}__`;
	if (mcpName.startsWith(prefix)) {
		return mcpName.slice(prefix.length);
	}
	return undefined;
}

/** Check if a tool name is an MCP-qualified pi tool name. */
export function isMcpPiTool(name: string): boolean {
	return name.startsWith(`mcp__${MCP_SERVER_NAME}__`);
}

/**
 * Create an in-process MCP server backed by pi's tool implementations.
 *
 * Each pi AgentTool is wrapped in an MCP tool definition with a Zod
 * schema derived from its TypeBox parameters and a handler that calls
 * the tool's execute method.
 *
 * @param piTools - pi's active AgentTool instances (from createReadTool etc.)
 * @returns MCP server config to pass to the SDK, plus the list of pi tool names
 */
export async function createPiMcpServer(
	piTools: AgentTool<any>[],
): Promise<{ server: McpSdkServerConfigWithInstance; toolNames: string[] }> {
	const sdk = await import("@anthropic-ai/claude-agent-sdk");
	const zod = await import("zod/v4");
	const z = zod.z ?? zod;

	const mcpTools: SdkMcpToolDefinition<any>[] = [];
	const toolNames: string[] = [];

	for (const piTool of piTools) {
		const schema = typeboxToZodShape(piTool, z);
		if (!schema) continue; // Skip tools we can't convert

		mcpTools.push({
			name: piTool.name,
			description: piTool.description,
			inputSchema: schema,
			handler: async (args: Record<string, unknown>) => {
				try {
					const result = await piTool.execute(
						`mcp-${piTool.name}`,
						args,
					);
					return {
						content: result.content
							.filter(
								(c: { type: string }) => c.type === "text",
							)
							.map((c: { type: string; text?: string }) => ({
								type: "text" as const,
								text: c.text ?? "",
							})),
					};
				} catch (e) {
					return {
						content: [
							{
								type: "text" as const,
								text:
									e instanceof Error
										? e.message
										: String(e),
							},
						],
						isError: true,
					};
				}
			},
		});
		toolNames.push(piTool.name);
	}

	const server = sdk.createSdkMcpServer({
		name: MCP_SERVER_NAME,
		version: "1.0.0",
		tools: mcpTools,
	});

	return { server, toolNames };
}

/**
 * Convert a pi AgentTool's TypeBox parameters to a Zod raw shape.
 *
 * Handles the common parameter types used by pi's built-in tools.
 * Returns undefined for tools with unsupported schema types.
 */
function typeboxToZodShape(
	tool: AgentTool<any>,
	z: any,
): Record<string, unknown> | undefined {
	const schema = tool.parameters;
	if (!schema || schema.type !== "object" || !schema.properties) {
		return undefined;
	}

	const shape: Record<string, unknown> = {};
	const required = new Set(schema.required ?? []);

	for (const [key, prop] of Object.entries(
		schema.properties as Record<string, any>,
	)) {
		let field = typeboxPropToZod(prop, z);
		if (!field) continue;

		if (!required.has(key)) {
			field = (field as any).optional();
		}
		if (prop.description) {
			field = (field as any).describe(prop.description);
		}
		shape[key] = field;
	}

	return shape;
}

/** Convert a single TypeBox property to a Zod type. */
function typeboxPropToZod(
	prop: any,
	z: any,
): unknown {
	switch (prop.type) {
		case "string":
			return z.string();
		case "number":
		case "integer":
			return z.number();
		case "boolean":
			return z.boolean();
		case "array": {
			if (prop.items) {
				const itemType = typeboxPropToZod(prop.items, z);
				if (itemType) return z.array(itemType as any);
			}
			return z.array(z.unknown());
		}
		case "object": {
			if (prop.properties) {
				const nested = typeboxToZodShape(
					{ parameters: prop } as any,
					z,
				);
				if (nested) return z.object(nested as any);
			}
			return z.record(z.string(), z.unknown());
		}
		default:
			return z.unknown();
	}
}
