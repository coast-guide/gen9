import { Download01Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";

import type { ChatFile } from "@/lib/agent";

/** "12 KB", "3.4 MB": a file's size as people read it. */
export function sizeOf(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The files a turn's environment shared (gen9-agent's chat_files.py), under its answer, each a
 * download. They stay after the environment is gone. */
export function SharedFiles({ threadId, files }: { threadId: string; files: ChatFile[] }) {
  if (!files.length) return null;
  return (
    <ul aria-label="Files" className="mt-3 flex flex-wrap gap-2">
      {files.map((file) => (
        <li key={file.id}>
          <a
            href={`/api/threads/${threadId}/files/${file.id}`}
            download={file.name.split("/").pop()}
            className="inline-flex items-center gap-2 rounded-xl border bg-card px-3 py-2 text-sm hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
          >
            <HugeiconsIcon icon={Download01Icon} strokeWidth={2} className="size-4 text-muted-foreground" aria-hidden />
            <span className="font-medium">{file.name}</span>
            <span className="text-muted-foreground">{sizeOf(file.size)}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}
