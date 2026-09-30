import type { DirectoryEntry } from "@/lib/agent";

const slug = (text: string) =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 32)
    .replace(/-+$/g, "");

// Parts of a registry name that say nothing about the server
const GENERIC = /^(mcp|mcp-server|server|servers|api|app|ai|com|io|dev|net|org|co|github|gitlab)$/;

/**
 * A connector name for a directory entry (lowercase letters, digits and hyphens, at most 32): its
 * title, else the registry name's own part, else the publisher's most specific part that isn't
 * generic (`com.notion/mcp` is "notion", `com.cloudflare.mcp/mcp` is "cloudflare").
 */
export function connectorName(entry: Pick<DirectoryEntry, "name" | "title">): string {
  if (entry.title && slug(entry.title)) return slug(entry.title);
  const [publisher, own = ""] = entry.name.split("/");
  if (slug(own) && !GENERIC.test(slug(own))) return slug(own);
  const part = publisher.split(".").reverse().find((p) => slug(p) && !GENERIC.test(slug(p)));
  return slug(part ?? "") || "connector";
}
