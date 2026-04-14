/**
 * Claude Memories Extension
 *
 * Mirrors Claude Code's memory behavior:
 * - Load only MEMORY.md into the system prompt
 * - Expose other memory files on demand via a tool/command
 *
 * Claude Code stores project memory under:
 *   ~/.claude/projects/<project-path>/memory/
 */

import * as fs from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { Type } from "@sinclair/typebox";

function cwdToClaudeProjectPath(cwd: string): string {
	const resolved = path.resolve(cwd);
	return resolved.replace(/[\/\.\s~]/g, "-");
}

function findMarkdownFiles(dir: string): string[] {
	if (!fs.existsSync(dir)) return [];

	return fs
		.readdirSync(dir, { withFileTypes: true })
		.filter((entry) => entry.isFile() && entry.name.endsWith(".md"))
		.map((entry) => entry.name)
		.sort();
}

function readMemoryFile(filepath: string): string | null {
	try {
		return fs.readFileSync(filepath, "utf-8");
	} catch {
		return null;
	}
}

function extractReferencedMarkdownFiles(content: string): string[] {
	const matches = Array.from(content.matchAll(/\[[^\]]+\]\(([^)]+\.md)\)/g));
	const files = matches
		.map((match) => path.basename(match[1]))
		.filter((file) => file !== "MEMORY.md");
	return Array.from(new Set(files)).sort();
}

function normalizeMemoryFilename(filename: string): string {
	const base = path.basename(filename.trim());
	return base.endsWith(".md") ? base : `${base}.md`;
}

export default function claudeMemoriesExtension(pi: ExtensionAPI) {
	let memoryDir = "";
	let memoryFiles: string[] = [];
	let memoryMd: string | null = null;
	let referencedFiles: string[] = [];

	pi.on("session_start", async (_event, ctx) => {
		const claudeProjectsDir = path.join(os.homedir(), ".claude", "projects");
		const projectPath = cwdToClaudeProjectPath(ctx.cwd);
		memoryDir = path.join(claudeProjectsDir, projectPath, "memory");
		memoryFiles = findMarkdownFiles(memoryDir);

		memoryMd = null;
		referencedFiles = [];

		if (memoryFiles.includes("MEMORY.md")) {
			memoryMd = readMemoryFile(path.join(memoryDir, "MEMORY.md"));
			referencedFiles = memoryMd ? extractReferencedMarkdownFiles(memoryMd) : [];
		}

		if (memoryMd) {
			const extraCount = Math.max(memoryFiles.length - 1, 0);
			ctx.ui.notify(
				extraCount > 0
					? `Loaded Claude MEMORY.md (${extraCount} additional memory file(s) available on demand)`
					: "Loaded Claude MEMORY.md",
				"info",
			);
		}
	});

	pi.on("before_agent_start", async (event) => {
		if (!memoryMd) return;

		const availableFiles = referencedFiles.length > 0 ? referencedFiles : memoryFiles.filter((f) => f !== "MEMORY.md");
		const availableFilesText =
			availableFiles.length > 0
				? `\nAdditional memory files are available on demand via the read_claude_memory tool:\n${availableFiles.map((f) => `- ${f}`).join("\n")}\n`
				: "";

		return {
			systemPrompt:
				event.systemPrompt +
				`

## Project Memory (from Claude Code)

Claude Code project memory exists for this repository. MEMORY.md is loaded below.${availableFilesText}
Load additional memory files only when they are relevant.

### MEMORY.md

${memoryMd}
`,
		};
	});

	pi.registerTool({
		name: "read_claude_memory",
		label: "Read Claude Memory",
		description: "Read a Claude Code project memory markdown file on demand",
		parameters: Type.Object({
			filename: Type.String({
				description: "Memory filename to read, for example project_go_migration.md",
			}),
		}),
		async execute(_toolCallId, params) {
			if (!memoryDir || memoryFiles.length === 0) {
				return {
					content: [{ type: "text", text: "No Claude memory files found for this project." }],
					details: {},
				};
			}

			const filename = normalizeMemoryFilename(params.filename);
			if (!memoryFiles.includes(filename)) {
				return {
					content: [
						{
							type: "text",
							text: `Memory file not found: ${filename}\n\nAvailable files:\n${memoryFiles.map((f) => `- ${f}`).join("\n")}`,
						},
					],
					details: {},
				};
			}

			const content = readMemoryFile(path.join(memoryDir, filename));
			if (!content) {
				return {
					content: [{ type: "text", text: `Failed to read memory file: ${filename}` }],
					details: {},
				};
			}

			return {
				content: [{ type: "text", text: content }],
				details: { filename, path: path.join(memoryDir, filename) },
			};
		},
	});

	pi.registerCommand("memories", {
		description: "List or view Claude Code memories for this project",
		handler: async (args, ctx) => {
			if (memoryFiles.length === 0) {
				ctx.ui.notify(`No memories found in ${memoryDir}`, "warning");
				return;
			}

			if (!args || args === "list") {
				const lines = [
					"Claude memories:",
					...memoryFiles.map((f) => (f === "MEMORY.md" ? `• ${f} (autoloaded)` : `• ${f}`)),
				];
				ctx.ui.notify(lines.join("\n"), "info");
				return;
			}

			const filename = normalizeMemoryFilename(args);
			if (!memoryFiles.includes(filename)) {
				ctx.ui.notify(`Memory not found: ${filename}`, "error");
				return;
			}

			const content = readMemoryFile(path.join(memoryDir, filename));
			if (!content) {
				ctx.ui.notify(`Failed to read: ${filename}`, "error");
				return;
			}

			ctx.ui.notify(`${filename}:\n\n${content}`, "info");
		},
	});

	pi.registerCommand("memories-path", {
		description: "Show the Claude Code memories directory path",
		handler: async (_args, ctx) => {
			ctx.ui.notify(`Memory path: ${memoryDir}`, "info");
		},
	});
}
