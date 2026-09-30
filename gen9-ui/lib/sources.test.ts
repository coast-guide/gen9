import { describe, expect, it } from "vitest";

import type { Message } from "@/lib/agent";
import { answerSources, turnOf } from "@/lib/sources";

describe("answerSources", () => {
  it("puts cited pages first, then pages opened, then pages searched, each once and clean", () => {
    const turn: Message[] = [
      {
        role: "assistant",
        content: "9.1.2 ([valkey.io](https://valkey.io/?utm_source=openai)) and [notes](https://github.com/valkey-io/valkey/releases).",
        citations: [{ url: "https://valkey.io/?utm_source=openai", title: "Valkey" }],
        steps: [
          { id: "1", name: "web_search", args: { query: "valkey" }, status: "success", sources: [{ url: "https://valkey.io/blog/" }, { url: "https://valkey.io/" }] },
          { id: "2", name: "web_open", args: { url: "https://www.redis.io/docs/" }, status: "success" },
        ],
      },
    ];
    const { cited, consulted } = answerSources(turn);
    // The provider cited valkey.io; nothing the turn found was the releases page (P5-C9)
    expect(cited).toEqual([
      { url: "https://valkey.io/", title: "Valkey", site: "valkey.io", place: "valkey.io", found: true },
      { url: "https://github.com/valkey-io/valkey/releases", title: null, site: "github.com", place: "github.com/valkey-io/valkey/releases", found: false },
    ]);
    expect(consulted.map((s) => s.url)).toEqual(["https://www.redis.io/docs/", "https://valkey.io/blog/"]);
    expect(consulted[0].site).toBe("redis.io");
  });

  it("finds nothing in an answer that didn't use the web", () => {
    expect(answerSources([{ role: "assistant", content: "391" }])).toEqual({ cited: [], consulted: [] });
  });

  it("lists no sources for a turn that neither searched nor opened pages, even when its answer links one", () => {
    const repeated: Message[] = [{ role: "assistant", content: "[safe](https://example.com) as you asked" }];
    expect(answerSources(repeated)).toEqual({ cited: [], consulted: [] });
  });

  it("keeps an answer's links as cited once its turn searched", () => {
    const turn: Message[] = [
      {
        role: "assistant",
        content: "See [the RFC](https://www.rfc-editor.org/rfc/rfc10017.html).",
        steps: [{ id: "1", name: "web_search", args: { query: "rfc 10017" }, status: "success", sources: [] }],
      },
    ];
    expect(answerSources(turn).cited.map((s) => s.site)).toEqual(["rfc-editor.org"]);
  });

  it("takes a turn's answers back to its question", () => {
    const messages: Message[] = [
      { role: "user", content: "q1" },
      { role: "assistant", content: "a1" },
      { role: "user", content: "q2" },
      { role: "assistant", content: "a2 first" },
      { role: "assistant", content: "a2 last" },
    ];
    expect(turnOf(messages, 4).map((m) => m.content)).toEqual(["a2 first", "a2 last"]);
    expect(turnOf(messages, 1).map((m) => m.content)).toEqual(["a1"]);
  });
});
