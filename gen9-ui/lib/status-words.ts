/** A run's state in the chat's words (docs/design/information-architecture.md, "Status words"). */
export const RUN_WORDS: Record<string, string> = {
  queued: "Starting",
  running: "Working",
  waiting: "Needs you",
  success: "Done",
  error: "Didn’t finish",
  cancelled: "Stopped",
  expired: "Stopped waiting",
};

/** A run that hasn't ended: starting, working, or waiting for the person. */
export const unfinished = (status: string | null | undefined) => !status || status === "queued" || status === "running" || status === "waiting";
