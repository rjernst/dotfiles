/**
 * Claude SDK Provider — pi extension
 *
 * Routes pi model execution through the Claude Agent SDK so pi can use
 * Claude subscription-backed usage instead of the normal Anthropic API
 * billing path.
 *
 * Use this provider when authenticated via OAuth (Claude subscription)
 * to avoid per-token API billing. OAuth tokens used with the regular
 * Anthropic API still incur API costs; the Agent SDK routes through
 * the subscription instead.
 *
 * Usage in pi:
 *   pi -e /path/to/pi/packages/claude-sdk-provider
 *   Then use /model to select claude-sdk/<model-id>
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";
import { registerClaudeSdkProvider } from "./src/provider.js";

export default function claudeSdkProviderExtension(pi: ExtensionAPI) {
	registerClaudeSdkProvider(pi);
}
