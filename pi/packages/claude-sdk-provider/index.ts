/**
 * Claude SDK Provider — pi extension
 *
 * Routes pi model execution through the Claude Agent SDK so pi can use
 * Claude subscription-backed usage instead of the normal Anthropic API
 * billing path.
 *
 * Model definitions are loaded from models.json at the package root.
 * To update available models, edit models.json — no recompile needed.
 */

import { readFileSync } from "fs";
import { dirname, join } from "path";
import { fileURLToPath } from "url";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import {
	registerClaudeSdkProvider,
	buildModelDefs,
	type ModelConfig,
	type McpServersConfig,
} from "./src/provider.js";

export default function claudeSdkProviderExtension(pi: ExtensionAPI) {
	const pkgRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
	const configs: ModelConfig[] = JSON.parse(
		readFileSync(join(pkgRoot, "models.json"), "utf-8"),
	);

	// Load MCP server configs (optional — file may not exist)
	let mcpServers: McpServersConfig | undefined;
	try {
		mcpServers = JSON.parse(
			readFileSync(join(pkgRoot, "mcp-servers.json"), "utf-8"),
		);
	} catch (err: unknown) {
		if ((err as NodeJS.ErrnoException).code !== "ENOENT") {
			throw err; // Re-throw parse errors, permission errors, etc.
		}
		// No mcp-servers.json — no MCP servers configured
	}

	registerClaudeSdkProvider(pi, buildModelDefs(configs), mcpServers);
}
