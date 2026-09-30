import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearDrafts, loadDraft, saveDraft } from "./drafts";

// sessionStorage as a browser has it
function storage(): Storage {
  const items = new Map<string, string>();
  const s = {
    getItem: (k: string) => items.get(k) ?? null,
    setItem: (k: string, v: string) => void items.set(k, String(v)),
    removeItem: (k: string) => void items.delete(k),
    clear: () => items.clear(),
    key: (i: number) => [...items.keys()][i] ?? null,
    get length() {
      return items.size;
    },
  };
  // Object.keys(sessionStorage) lists its items in a browser
  return new Proxy(s, { ownKeys: () => [...items.keys()], getOwnPropertyDescriptor: (_, k) => (items.has(String(k)) ? { enumerable: true, configurable: true, value: items.get(String(k)) } : undefined) }) as Storage;
}

describe("drafts kept in the tab", () => {
  beforeEach(() => vi.stubGlobal("sessionStorage", storage()));
  afterEach(() => vi.unstubAllGlobals());

  it("keeps a draft per person and chat, and an empty one removes it", () => {
    saveDraft("alan", null, "half a question");
    saveDraft("alan", "chat-1", "about chat one");
    expect(loadDraft("alan", null)).toBe("half a question");
    expect(loadDraft("alan", "chat-1")).toBe("about chat one");
    expect(loadDraft("ada", null)).toBe("");
    saveDraft("alan", "chat-1", "  ");
    expect(loadDraft("alan", "chat-1")).toBe("");
  });

  it("sign-out clears everyone's drafts on the tab, and only drafts", () => {
    saveDraft("alan", null, "a");
    saveDraft("ada", "chat-2", "b");
    sessionStorage.setItem("something-else", "stays");
    clearDrafts();
    expect(loadDraft("alan", null)).toBe("");
    expect(loadDraft("ada", "chat-2")).toBe("");
    expect(sessionStorage.getItem("something-else")).toBe("stays");
  });

  it("keeps nothing, and doesn't throw, where storage is refused", () => {
    vi.stubGlobal("sessionStorage", {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("SecurityError");
      },
      removeItem: () => {
        throw new Error("SecurityError");
      },
    });
    expect(() => saveDraft("alan", null, "x")).not.toThrow();
    expect(loadDraft("alan", null)).toBe("");
    expect(() => clearDrafts()).not.toThrow();
  });
});
