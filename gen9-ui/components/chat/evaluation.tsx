import type { Evaluation } from "@/lib/agent";

/** The verdict's line, in the chat's words (docs/design/screens/chat.md, "Checked against its rubric"). */
export function verdictWords(evaluation: Evaluation) {
  const met = evaluation.criteria.filter((c) => c.met).length;
  const of = `${met} of ${evaluation.criteria.length}`;
  if (evaluation.result === "satisfied") return `Meets its rubric · ${of}`;
  if (evaluation.result === "failed") return "Its rubric doesn’t apply to what was asked";
  return `Short of its rubric · ${of} met`;
}

/**
 * Under a scheduled task's answer, when the task has a rubric (gen9-agent's outcomes.py): what a
 * separate grader found, criterion by criterion. Folded to one line; met and not met are said in
 * words, not by colour alone.
 */
export function EvaluationNote({ evaluation }: { evaluation: Evaluation }) {
  return (
    <details className="mt-3 text-sm">
      <summary className="cursor-pointer text-muted-foreground select-none hover:text-foreground">
        {verdictWords(evaluation)}
        {evaluation.iteration > 0 && ` (try ${evaluation.iteration + 1})`}
      </summary>
      <div className="mt-2 grid gap-2 border-l-2 pl-3">
        <p>{evaluation.explanation}</p>
        <ul aria-label="Criteria" className="grid gap-1.5">
          {evaluation.criteria.map((c, i) => (
            <li key={i}>
              <span className={c.met ? "font-medium" : "font-medium text-destructive"}>{c.met ? "Met" : "Not met"}:</span> {c.criterion}
              <span className="block text-muted-foreground">{c.why}</span>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}
