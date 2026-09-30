import { describe, expect, it } from "vitest";

import type { Question } from "@/lib/agent";
import { answerOf, answersIn, emptyDraft, OTHER, unanswered } from "@/lib/questions";

const city: Question = { question: "Which city?", type: "multiple_choice", choices: ["Paris", "Rome"], required: true };
const note: Question = { question: "Anything else?", type: "text", choices: [], required: false };

describe("answers to the agent's questions", () => {
  it("takes the choice, or the person's own text for Other and for text questions", () => {
    expect(answerOf(city, { choice: "Rome", text: "" })).toBe("Rome");
    expect(answerOf(city, { choice: OTHER, text: "  Lisbon " })).toBe("Lisbon");
    expect(answerOf(note, { choice: null, text: " no " })).toBe("no");
  });

  it("needs every required question answered, and nothing more", () => {
    expect(unanswered([city, note], [emptyDraft(), emptyDraft()])).toEqual([0]);
    expect(unanswered([city, note], [{ choice: OTHER, text: " " }, emptyDraft()])).toEqual([0]);
    expect(unanswered([city, note], [{ choice: "Paris", text: "" }, emptyDraft()])).toEqual([]);
  });

  it("reads the answers back from a question step's output", () => {
    expect(answersIn("Q: Which city?\nA: Rome\n\nQ: Anything else?\nA: (not answered)")).toEqual(["Rome", "(not answered)"]);
    expect(answersIn("Q: Two lines?\nA: yes\nand more")).toEqual(["yes\nand more"]);
    expect(answersIn(null)).toEqual([]);
  });
});
