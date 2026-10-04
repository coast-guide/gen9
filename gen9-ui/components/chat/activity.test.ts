import { describe, expect, it } from "vitest";

import { clip, describeStep, stepsSummary, stepWords } from "@/components/chat/activity";
import type { Step } from "@/lib/agent";

const step = (name: string, args: unknown): Step => ({ id: "1", name, args, status: "success" });

describe("describeStep", () => {
  it("says what each built-in tool did", () => {
    expect(describeStep(step("web_search", { query: "postgres 18" }))).toBe("Searched the web: postgres 18");
    expect(describeStep(step("web_search", {}))).toBe("Searched the web");
    expect(describeStep(step("web_open", { url: "https://postgresql.org" }))).toBe("Opened https://postgresql.org");
    expect(describeStep(step("web_find", { pattern: "18.1", url: "https://postgresql.org" }))).toBe("Looked for “18.1” in https://postgresql.org");
    expect(describeStep(step("read_file", { file_path: "/notes.md" }))).toBe("Read /notes.md");
    expect(describeStep(step("write_file", { file_path: "/a.txt" }))).toBe("Wrote /a.txt");
    expect(describeStep(step("edit_file", { file_path: "/a.txt" }))).toBe("Edited /a.txt");
    expect(describeStep(step("grep", { pattern: "TODO" }))).toBe("Searched files for TODO");
    expect(describeStep(step("task", { description: "Check the sources" }))).toBe("Asked a helper: Check the sources");
    expect(describeStep(step("task", { description: "Check the date", subagent_type: "general-purpose" }))).toBe("Asked a helper: Check the date");
    expect(describeStep(step("task", { description: "Check the date", subagent_type: "fact-checker" }))).toBe("Asked the fact checker: Check the date");
  });

  it("cuts a long task or command at a word, and says so", () => {
    const claim = "Fact-check this claim against primary sources: “Valkey 9.1.2 was released on 1 September 2026.” Determine confirmed, contradicted or unverified.";
    const label = describeStep(step("task", { description: claim, subagent_type: "fact-checker" }));
    expect(label).toBe("Asked the fact checker: Fact-check this claim against primary sources: “Valkey 9.1.2 was released on 1 September 2026.” Determine confirmed…");
    expect(describeStep(step("execute", { command: `echo ${"x".repeat(200)}` }))).toBe(`Ran: echo ${"x".repeat(114)}…`);
    expect(clip("short")).toBe("short");
    expect(clip("a".repeat(120))).toBe("a".repeat(120));
    expect(clip("a".repeat(121))).toBe(`${"a".repeat(119)}…`);
  });

  it("says what a failed step tried, not what it would have done", () => {
    const failed: Step = { id: "1", name: "edit_file", args: { file_path: "/skills/research-brief/SKILL.md" }, status: "error" };
    expect(describeStep(failed)).toBe("Couldn’t edit /skills/research-brief/SKILL.md");
    expect(describeStep({ ...failed, name: "travel__plan_trip", args: {} })).toBe("Couldn’t use travel: plan trip");
  });

  it("says a connector's tool waits for the person while its server asks them", () => {
    const asking: Step = { id: "1", name: "travel__plan_trip", args: {}, status: "running" };
    expect(stepWords(asking, [], true)).toEqual({ text: "Waiting for you: use travel: plan trip", waits: true });
    expect(stepWords(asking, [], false).waits).toBe(false);
  });

  it("names the person's memory instead of its file", () => {
    expect(describeStep(step("edit_file", { file_path: "/memories/AGENTS.md" }))).toBe("Updated your memory");
    expect(describeStep(step("write_file", { file_path: "/memories/AGENTS.md" }))).toBe("Updated your memory");
    expect(describeStep(step("read_file", { file_path: "/memories/AGENTS.md" }))).toBe("Read your memory");
  });

  it("names a skill the agent used", () => {
    expect(describeStep(step("read_file", { file_path: "/skills/research-brief/SKILL.md" }))).toBe("Used the research brief skill");
    expect(describeStep(step("read_file", { file_path: "/skills/research-brief/references/x.md" }))).toBe("Read /skills/research-brief/references/x.md");
  });

  it("says what the agent asked the person", () => {
    expect(describeStep(step("ask_user", { questions: [{ question: "Which city?" }] }))).toBe("Asked you: Which city?");
    expect(describeStep(step("ask_user", { questions: [{ question: "Which city?" }, { question: "When?" }] }))).toBe(
      "Asked you: Which city? (and 1 more)",
    );
    expect(describeStep(step("ask_user", {}))).toBe("Asked you a question");
  });

  it("says what the person declined", () => {
    const denied = { ...step("edit_file", { file_path: "/memories/AGENTS.md" }), status: "declined" as const };
    expect(describeStep(denied)).toBe("You declined: update your memory");
  });

  it("says what the agent did with its background tasks", () => {
    expect(describeStep(step("start_async_task", { description: "Compare three plans\nwith details", subagent_type: "gen9" }))).toBe(
      "Started in the background: Compare three plans",
    );
    expect(describeStep(step("check_async_task", { task_id: "t" }))).toBe("Checked a background task");
    expect(describeStep(step("update_async_task", { task_id: "t", message: "m" }))).toBe("Gave a background task new instructions");
    expect(describeStep(step("cancel_async_task", { task_id: "t" }))).toBe("Stopped a background task");
    expect(describeStep(step("list_async_tasks", {}))).toBe("Looked at the background tasks");
  });

  it("says it looked through the person's past chats", () => {
    expect(describeStep(step("search_past_chats", { query: "tides" }))).toBe("Searched your past chats: tides");
    expect(describeStep(step("recent_chats", {}))).toBe("Looked at your recent chats");
  });

  it("names a connector's tool with its connector", () => {
    expect(describeStep(step("deepwiki__read_wiki_structure", { repoName: "x/y" }))).toBe("Used deepwiki: read wiki structure");
  });

  it("falls back to the tool's name, whatever its arguments", () => {
    expect(describeStep(step("lookup_city", "not an object"))).toBe("lookup city");
    expect(describeStep(step("ls", null))).toBe("Listed files");
  });
});

describe("stepsSummary", () => {
  it("counts tools, and names a plan made without any", () => {
    expect(stepsSummary(0, true)).toBe("Made a plan");
    expect(stepsSummary(1, true)).toBe("Used 1 tool and a plan");
    expect(stepsSummary(3, false)).toBe("Used 3 tools");
  });
});

describe("a step an approval holds", () => {
  const running = (name: string, args: unknown): Step => ({ id: "2", name, args, status: "running" });

  it("reads as waiting for the person, as its card reads, not as used", () => {
    const call = running("context7__resolve_library_id", { libraryName: "fastapi", query: "x" });
    // The same arguments in another order are the same call
    const held = [{ name: "context7__resolve_library_id", args: { query: "x", libraryName: "fastapi" } }];
    expect(stepWords(call, held)).toEqual({ text: "Waiting for you: use context7: resolve library id", waits: true });
    expect(stepWords(running("execute", { command: "ls" }), [{ name: "execute", args: { command: "ls" } }]).text).toBe(
      "Waiting for you: run a command in this chat's environment",
    );
    expect(stepWords(running("edit_file", { file_path: "/memories/AGENTS.md" }), [{ name: "edit_file", args: { file_path: "/memories/AGENTS.md" } }]).text).toBe(
      "Waiting for you: update your memory",
    );
  });

  it("leaves other steps as they are: another call, one that finished, none held", () => {
    const held = [{ name: "execute", args: { command: "ls" } }];
    expect(stepWords(running("execute", { command: "pwd" }), held)).toEqual({ text: "Ran: pwd", waits: false });
    expect(stepWords({ ...running("execute", { command: "ls" }), status: "success" }, held).text).toBe("Ran: ls");
    expect(stepWords(running("context7__query_docs", {})).text).toBe("Used context7: query docs");
  });
});
