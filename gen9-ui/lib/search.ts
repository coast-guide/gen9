import type { SearchHit, SearchMode } from "@/lib/agent";

/** The search screen's modes, in order, with the words people see (docs/design/screens/search.md). */
export const MODES: { value: SearchMode; label: string }[] = [
  { value: "hybrid", label: "All" },
  { value: "keyword", label: "Words" },
  { value: "semantic", label: "Meaning" },
  { value: "fuzzy", label: "Title" },
];

export function parseMode(value: unknown): SearchMode {
  return MODES.some((mode) => mode.value === value) ? (value as SearchMode) : "hybrid";
}

/** "All" answered by words alone: search by meaning was unavailable (gen9-agent's fallback). */
export function byWordsOnly(mode: SearchMode, hits: SearchHit[]): boolean {
  return mode === "hybrid" && hits.length > 0 && hits.every((hit) => hit.ranked_by === "keyword");
}
