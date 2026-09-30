// Client-safe (lib/agent.ts is server-only; its types are erased on import)
import type { InputRequest } from "@/lib/agent";

/** Every kind of input request this app can answer: the chat shows a card for each. */
export const ANSWERABLE_KINDS = ["question", "approval", "elicitation", "retry"] as const satisfies readonly InputRequest["kind"][];
// A kind added to InputRequest but not to ANSWERABLE_KINDS fails to compile here, instead of the
// chat leaving such runs waiting unseen
const everyKindAnswerable: Exclude<InputRequest["kind"], (typeof ANSWERABLE_KINDS)[number]> extends never ? true : never = true;
void everyKindAnswerable;
