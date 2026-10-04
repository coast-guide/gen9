import type { Action } from "@/lib/agent";

/** The person's memory, which the agent loads and edits (gen9-agent's memory.py). */
export const MEMORY_FILE = "/memories/AGENTS.md";
// The memory file's first line, which only the agent reads (gen9-agent's memory.STARTER)
const STARTER = "# What Gen9 remembers about this person";

const text = (value: unknown) => (typeof value === "string" ? value : "");

/** What an action does, as the end of "Gen9 wants to …" or "You declined: …". */
export function actionWords(action: Pick<Action, "name" | "args">): string {
  const args = (action.args ?? {}) as Record<string, unknown>;
  if ((action.name === "edit_file" || action.name === "write_file") && text(args.file_path) === MEMORY_FILE) {
    return "update your memory";
  }
  // A command in the chat's environment (gen9-agent's environments.py)
  if (action.name === "execute") return "run a command in this chat's environment";
  // A file, by its path: "use write file" said nothing of which (manual-e2e.md, P8-O3)
  const verb = { edit_file: "edit", write_file: "write", read_file: "read" }[action.name];
  if (verb) return `${verb} ${text(args.file_path)}`;
  // A connector's tool (gen9-agent's connectors.py): <connector>__<tool>
  const [connector, tool] = action.name.split("__");
  if (tool) return `use ${connector}: ${tool.replaceAll("_", " ")}`;
  return `use ${action.name.replaceAll("_", " ")}`;
}

const lines = (value: unknown) =>
  text(value)
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && line !== STARTER);

/** What an action would change, line by line: added, and removed (a file edit or write). */
export function changesOf(action: Pick<Action, "name" | "args">): { added: string[]; removed: string[] } {
  const args = (action.args ?? {}) as Record<string, unknown>;
  const before = lines(action.name === "edit_file" ? args.old_string : "");
  const after = lines(action.name === "edit_file" ? args.new_string : args.content);
  return { added: after.filter((l) => !before.includes(l)), removed: before.filter((l) => !after.includes(l)) };
}

/** The reason the person gave when they declined (from the result the agent got), if any. */
export function reasonIn(output: string | null | undefined): string | null {
  return output?.match(/with reason: ([\s\S]+)$/)?.[1]?.trim() || null;
}
