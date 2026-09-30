import { describe, expect, it } from "vitest";

import { connectorName } from "@/lib/directory";

describe("a connector name for a directory entry", () => {
  it("uses the title, else the registry name's own part, else its publisher", () => {
    expect(connectorName({ name: "com.notion/mcp", title: null })).toBe("notion");
    expect(connectorName({ name: "com.cloudflare.mcp/mcp", title: null })).toBe("cloudflare");
    expect(connectorName({ name: "app.linear/linear", title: null })).toBe("linear");
    expect(connectorName({ name: "io.github.acme/Notes_Server", title: null })).toBe("notes-server");
    expect(connectorName({ name: "com.example/mcp", title: "Example Docs" })).toBe("example-docs");
  });

  it("fits the connector name rules", () => {
    const long = connectorName({ name: "io.github.x/a-very-long-server-name-that-goes-on-and-on-and-on", title: null });
    expect(long.length).toBeLessThanOrEqual(32);
    expect(long).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
    expect(connectorName({ name: "x/", title: "!!!" })).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
  });
});
