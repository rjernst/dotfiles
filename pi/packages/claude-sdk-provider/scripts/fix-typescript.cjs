#!/usr/bin/env node
/**
 * fix-typescript.js
 *
 * Validates that the installed TypeScript compiler works correctly and repairs
 * it if not. In some sandboxed Linux environments, npm's tar extraction can
 * corrupt large files with null bytes. This script detects that condition and
 * re-extracts TypeScript from a fresh tarball via `npm pack` into /tmp, then
 * copies it cleanly into node_modules.
 *
 * Called automatically as the npm `postinstall` lifecycle hook.
 */

"use strict";

const path = require("path");
const fs = require("fs");
const { spawnSync } = require("child_process");

const pkgRoot = path.resolve(__dirname, "..");
const tscBin = path.join(pkgRoot, "node_modules", ".bin", "tsc");

function typescriptWorks() {
	try {
		const result = spawnSync("node", [tscBin, "--version"], {
			encoding: "utf8",
			cwd: pkgRoot,
			timeout: 10000,
		});
		return result.status === 0 && result.stdout.trim().startsWith("Version");
	} catch {
		return false;
	}
}

function getInstalledTypescriptVersion() {
	try {
		const pkgJson = path.join(pkgRoot, "node_modules", "typescript", "package.json");
		const pkg = JSON.parse(fs.readFileSync(pkgJson, "utf8"));
		return pkg.version;
	} catch {
		return null;
	}
}

if (typescriptWorks()) {
	process.exit(0);
}

const version = getInstalledTypescriptVersion() || "5.4.5";
console.log(`[fix-typescript] TypeScript ${version} installation appears corrupted. Re-extracting...`);

const tmpDir = path.join("/tmp", `ts-fix-${Date.now()}`);
try {
	fs.mkdirSync(tmpDir, { recursive: true });

	let r = spawnSync("npm", ["pack", `typescript@${version}`, "--quiet"], {
		cwd: tmpDir,
		encoding: "utf8",
		stdio: "pipe",
	});
	if (r.status !== 0) {
		console.error("[fix-typescript] npm pack failed:", r.stderr);
		process.exit(1);
	}

	const tarball = path.join(tmpDir, `typescript-${version}.tgz`);
	r = spawnSync("tar", ["xzf", tarball, "-C", tmpDir], { stdio: "pipe" });
	if (r.status !== 0) {
		console.error("[fix-typescript] tar extraction failed");
		process.exit(1);
	}

	const tsDir = path.join(pkgRoot, "node_modules", "typescript");
	r = spawnSync("rm", ["-rf", tsDir], { stdio: "pipe" });
	if (r.status !== 0) {
		console.error("[fix-typescript] failed to remove corrupt typescript directory");
		process.exit(1);
	}
	r = spawnSync("cp", ["-r", path.join(tmpDir, "package"), tsDir], { stdio: "pipe" });
	if (r.status !== 0) {
		console.error("[fix-typescript] copy failed");
		process.exit(1);
	}

	if (typescriptWorks()) {
		console.log("[fix-typescript] TypeScript repaired successfully.");
	} else {
		console.error("[fix-typescript] Repair failed — TypeScript still not working.");
		process.exit(1);
	}
} finally {
	spawnSync("rm", ["-rf", tmpDir]);
}
