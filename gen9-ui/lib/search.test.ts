import { describe, expect, it } from "vitest";

import type { SearchHit } from "@/lib/agent";
import { byWordsOnly, parseMode } from "@/lib/search";

const hit = (thread_id: string, ranked_by: SearchHit["ranked_by"] = "hybrid"): SearchHit => ({
  thread_id,
  title: `Chat ${thread_id}`,
  run_id: null,
  snippet: null,
  score: 1,
  ranked_by,
  created_at: "2026-09-25T00:00:00Z",
});

describe("search", () => {
  it("falls back to All for a mode it doesn't know", () => {
    expect(parseMode("semantic")).toBe("semantic");
    expect(parseMode("fuzzy")).toBe("fuzzy");
    expect(parseMode("nope")).toBe("hybrid");
    expect(parseMode(undefined)).toBe("hybrid");
    expect(parseMode(["keyword"])).toBe("hybrid");
  });

  it("notices All answered by words alone", () => {
    expect(byWordsOnly("hybrid", [hit("a", "keyword"), hit("b", "keyword")])).toBe(true);
    expect(byWordsOnly("hybrid", [hit("a", "hybrid")])).toBe(false);
    expect(byWordsOnly("keyword", [hit("a", "keyword")])).toBe(false);
    expect(byWordsOnly("hybrid", [])).toBe(false);
  });
});
