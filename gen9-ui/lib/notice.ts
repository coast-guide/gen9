// A background task's notice (gen9-agent's background.py), as the chat shows it: without the line
// naming the task's id, and without the block marks and note that tell the agent its answer is
// data, not instructions (docs/plans/gen9-learn.md, M9, F19). Older notices had neither.
export function noticeText(content: string): string {
  return withoutMarks(
    content
      .replace(/^\[Background task [^\]]*\] task_id: \S+\n/, "")
      .replace(/^(.*\n\n)<task-answer>\n/, "$1")
      .replace(/\n<\/task-answer>\n[^\n]*/, ""),
  );
}

/** Markdown's inline marks taken out, as plain text shows them: "**9.1.2**" read as 9.1.2, a
 *  link as its words (manual-e2e.md, P8-O3). */
export function withoutMarks(text: string): string {
  return text
    .replace(/\[([^\]]+)\]\([^)\s]+\)/g, "$1")
    .replace(/(\*\*|__)(?=\S)([\s\S]*?\S)\1/g, "$2")
    .replace(/`([^`\n]+)`/g, "$1")
    .replace(/^#{1,6} +/gm, "");
}

// A grader's findings for a scheduled task's next try (gen9-agent's outcomes.py), as the chat
// shows them: without the block marks and note that tell the agent they are data.
export function revisionText(content: string): string {
  return content.replace(/<grader-findings>\n/, "").replace(/\n<\/grader-findings>\n[^\n]*/, "");
}
