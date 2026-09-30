"use client";

import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import type { QuestionRequest } from "@/lib/agent";
import { answerOf, type Draft, emptyDraft, OTHER, unanswered } from "@/lib/questions";

const MAX_ANSWER = 2000;

const PILL =
  "inline-flex min-h-9 cursor-pointer items-center rounded-full border px-4 text-sm transition-colors pointer-coarse:min-h-11 " +
  "hover:bg-muted peer-checked:border-primary peer-checked:bg-primary peer-checked:text-primary-foreground " +
  "peer-focus-visible:ring-[3px] peer-focus-visible:ring-ring/50";

/**
 * What the agent asks mid-task (docs/design/screens/chat.md, "Questions"): each question with its
 * choices ("Other" last) or a text field, and one Send answer. `answer` sends the answers and
 * returns what went wrong, or null; the card then disappears as the run goes on.
 */
export function QuestionCard({
  request,
  answer,
  takeFocus,
}: {
  request: QuestionRequest;
  answer: (answers: string[]) => Promise<string | null>;
  takeFocus: boolean;
}) {
  const { questions } = request;
  const [drafts, setDrafts] = useState<Draft[]>(() => questions.map(emptyDraft));
  const [sending, setSending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const card = useRef<HTMLFormElement>(null);

  useEffect(() => {
    // The composer the person was typing in is now disabled: continue here
    if (takeFocus) card.current?.querySelector<HTMLElement>("input, textarea")?.focus();
  }, [takeFocus]);

  const edit = (i: number, change: Partial<Draft>) => setDrafts((d) => d.map((draft, j) => (j === i ? { ...draft, ...change } : draft)));
  const many = questions.length > 1;

  async function submit() {
    const missing = unanswered(questions, drafts);
    if (missing.length) {
      setProblem(many ? `Answer question ${missing[0] + 1} first.` : "Answer the question first.");
      card.current?.querySelectorAll<HTMLFieldSetElement>("fieldset")[missing[0]]?.querySelector<HTMLElement>("input, textarea")?.focus();
      return;
    }
    setSending(true);
    setProblem(null);
    const wrong = await answer(questions.map((q, i) => answerOf(q, drafts[i])));
    if (wrong) {
      setProblem(wrong);
      setSending(false);
    }
  }

  return (
    <form
      ref={card}
      aria-label="Gen9 needs your answer"
      className="mb-4 grid gap-5 rounded-2xl border bg-card p-4 shadow-sm sm:p-5"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <p className="text-sm font-medium">Gen9 needs your answer</p>
      {questions.map((q, i) => (
        <fieldset key={i} className="grid min-w-0 gap-2.5" disabled={sending}>
          <legend className="mb-2.5 text-base leading-snug">
            {many && <span className="text-muted-foreground">{i + 1}. </span>}
            {q.question}
            {!q.required && <span className="ml-1.5 text-sm text-muted-foreground">(optional)</span>}
          </legend>
          {q.type === "multiple_choice" ? (
            <>
              <div className="flex flex-wrap gap-1.5">
                {[...q.choices, OTHER].map((choice) => (
                  <label key={choice} className="relative">
                    <input
                      type="radio"
                      name={`${request.id}-${i}`}
                      value={choice}
                      checked={drafts[i].choice === choice}
                      onChange={() => edit(i, { choice })}
                      className="peer sr-only"
                    />
                    <span className={PILL}>{choice === OTHER ? "Other" : choice}</span>
                  </label>
                ))}
              </div>
              {drafts[i].choice === OTHER && (
                <Input
                  aria-label={`Your answer to: ${q.question}`}
                  value={drafts[i].text}
                  onChange={(e) => edit(i, { text: e.target.value })}
                  maxLength={MAX_ANSWER}
                  placeholder="Your answer"
                  autoFocus
                />
              )}
            </>
          ) : (
            <Textarea
              aria-label={`Your answer to: ${q.question}`}
              value={drafts[i].text}
              onChange={(e) => edit(i, { text: e.target.value })}
              maxLength={MAX_ANSWER}
              rows={2}
              placeholder="Your answer"
              className="min-h-11"
            />
          )}
        </fieldset>
      ))}
      <div className="flex flex-wrap items-center justify-end gap-3">
        {problem && (
          <p role="alert" className="mr-auto text-sm text-destructive">
            {problem}
          </p>
        )}
        <Button type="submit" disabled={sending}>
          {sending && <Spinner aria-hidden />}
          Send answer
        </Button>
      </div>
    </form>
  );
}
