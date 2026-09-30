import "server-only";

import { env } from "@/lib/env";

/** The origin a connector's Views run on (MCP Apps): the sandbox's template with the connector's id,
 * so one connector's View never shares an origin, and its storage, with another's. Null when Views
 * are off. */
export function sandboxOrigin(connectorId: string): string | null {
  const template = env().MCP_APPS_SANDBOX_URL;
  return template ? template.replaceAll("{id}", connectorId.replaceAll("-", "").toLowerCase()) : null;
}
