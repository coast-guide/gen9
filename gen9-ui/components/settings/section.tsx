import { cn } from "@/lib/utils";

/** A settings group: a quiet heading, then rows separated by hairlines inside one sheet. */
export function Section({ title, description, children }: { title: string; description?: string; children: React.ReactNode }) {
  return (
    // min-w-0: one long word (a skill's description, at 200% text) had widened every group past a phone's
    // width; the sheet breaks such words instead (P2-J2)
    <section className="grid min-w-0 gap-3">
      <div className="min-w-0 px-1">
        <h2 className="text-[0.9375rem] font-semibold">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      <div className="min-w-0 divide-y rounded-2xl border bg-card wrap-break-word">{children}</div>
    </section>
  );
}

/** One setting: label and state on the left, the action on the right; wraps only when it doesn't fit. */
export function Row({
  label,
  value,
  action,
  className,
}: {
  label: string;
  value?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-4 sm:px-5", className)}>
      {/* A basis, not a minimum: in rem a minimum outgrew a phone at 200% text (P2-J2) */}
      <div className="min-w-0 flex-1 basis-48">
        <p className="text-sm font-medium">{label}</p>
        {value && <div className="mt-0.5 text-sm wrap-anywhere text-muted-foreground">{value}</div>}
      </div>
      {action && <div className="min-w-0 max-w-full">{action}</div>}
    </div>
  );
}
