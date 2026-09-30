import { describe, expect, it } from "vitest";

import { actionWords, changesOf, MEMORY_FILE, reasonIn } from "@/lib/approvals";

describe("approvals in words", () => {
  it("names a command in the chat's environment", () => {
    expect(actionWords({ name: "execute", args: { command: "python -c 'print(1)'" } })).toBe("run a command in this chat's environment");
  });

  it("names a memory write, and any other tool by its name", () => {
    expect(actionWords({ name: "edit_file", args: { file_path: MEMORY_FILE } })).toBe("update your memory");
    expect(actionWords({ name: "write_file", args: { file_path: MEMORY_FILE } })).toBe("update your memory");
    expect(actionWords({ name: "send_email", args: {} })).toBe("use send email");
    expect(actionWords({ name: "deepwiki__ask_wiki_question", args: {} })).toBe("use deepwiki: ask wiki question");
  });

  it("shows what a memory edit adds and removes, without the file's own heading", () => {
    const edit = {
      name: "edit_file",
      args: {
        file_path: MEMORY_FILE,
        old_string: "# What Gen9 remembers about this person\n\n- Lives in Leeds\n",
        new_string: "# What Gen9 remembers about this person\n\n- Lives in York\n- Favourite colour: teal\n",
      },
    };
    expect(changesOf(edit)).toEqual({ added: ["- Lives in York", "- Favourite colour: teal"], removed: ["- Lives in Leeds"] });
    expect(changesOf({ name: "write_file", args: { content: "- Likes tea" } })).toEqual({ added: ["- Likes tea"], removed: [] });
  });

  it("reads the reason back from a declined call", () => {
    expect(reasonIn("User rejected the tool call for `edit_file` with reason: not now")).toBe("not now");
    expect(reasonIn("User rejected the tool call for `edit_file` with id e1. The tool was not executed.")).toBeNull();
  });
});
