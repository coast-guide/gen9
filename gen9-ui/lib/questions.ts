import type { Question } from "@/lib/agent";

/** The "Other" choice of a multiple-choice question: the person types their own answer. */
export const OTHER = "\u0000other";

/** What the person picked or typed for one question: a choice (or OTHER) and their own text. */
export type Draft = { choice: string | null; text: string };

export const emptyDraft = (): Draft => ({ choice: null, text: "" });

/** The answer a draft gives to its question: the choice, or the text for "Other" and text questions. */
export function answerOf(question: Question, draft: Draft): string {
  if (question.type === "multiple_choice" && draft.choice !== OTHER) return draft.choice ?? "";
  return draft.text.trim();
}

/** The questions (0-based) that still need an answer before the answers can be sent. */
export function unanswered(questions: Question[], drafts: Draft[]): number[] {
  return questions.flatMap((q, i) => (q.required && !answerOf(q, drafts[i] ?? emptyDraft()) ? [i] : []));
}

/** The answers in a question step's output ("Q: …\nA: …" per question, gen9-agent's questions.py). */
export function answersIn(output: string | null | undefined): string[] {
  if (!output) return [];
  return output
    .split("\n\n")
    .map((pair) => pair.match(/^Q: [\s\S]*?\nA: ([\s\S]*)$/)?.[1]?.trim())
    .filter((answer): answer is string => answer !== undefined);
}
