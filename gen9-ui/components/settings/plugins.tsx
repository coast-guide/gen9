"use client";

import { useState, useTransition } from "react";

import { addMyPlugin, removeMyPlugin } from "@/app/(app)/settings/actions";
import { Button } from "@/components/ui/button";
import type { MyPlugin, MySkill } from "@/lib/agent";

const plural = (n: number, one: string) => `${n} ${one}${n === 1 ? "" : "s"}`;

function PluginRow({ plugin }: { plugin: MyPlugin }) {
  const [problem, setProblem] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const name = plugin.title ?? plugin.name;
  const brings = [plugin.skills.length ? plural(plugin.skills.length, "skill") : null, plugin.connectors ? plural(plugin.connectors, "connector") : null]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="grid gap-2 px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1 basis-48">
          <p className="text-sm font-medium">
            {name}
            {plugin.version && <span className="font-normal text-muted-foreground"> {plugin.version}</span>}
          </p>
          <p className="text-sm text-muted-foreground">
            {plugin.source}
            {brings ? ` · ${brings}` : ""}
          </p>
          {plugin.description && <p className="mt-1 text-sm">{plugin.description}</p>}
          {plugin.waiting && (
            <p className="mt-1 text-sm text-muted-foreground">It changed, and an admin hasn’t looked at it yet: Gen9 doesn’t use it until they do.</p>
          )}
        </div>
        {plugin.for_everyone ? (
          <span className="text-sm text-muted-foreground">Everyone has it</span>
        ) : (
          <Button
            type="button"
            variant={plugin.added ? "ghost" : "default"}
            size="sm"
            disabled={pending}
            aria-label={`${plugin.added ? "Remove" : "Add"} ${name}`}
            onClick={() =>
              start(async () => {
                const result = plugin.added ? await removeMyPlugin(plugin.id) : await addMyPlugin(plugin.id);
                setProblem(result?.error ?? null);
              })
            }
          >
            {plugin.added ? "Remove" : "Add"}
          </Button>
        )}
      </div>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
    </div>
  );
}

export function PluginSettings({ plugins }: { plugins: MyPlugin[] }) {
  return (
    <ul aria-label="Plugins" className="divide-y">
      {plugins.map((plugin) => (
        <li key={plugin.id}>
          <PluginRow plugin={plugin} />
        </li>
      ))}
    </ul>
  );
}

export function SkillList({ skills }: { skills: MySkill[] }) {
  return (
    <ul aria-label="Skills" className="divide-y">
      {skills.map((skill) => (
        <li key={skill.name} className="px-4 py-3.5 text-sm sm:px-5">
          <p className="font-medium">
            <span className="font-mono text-xs">{skill.name}</span>
            <span className="font-normal text-muted-foreground"> · {skill.plugin ? `From ${skill.plugin}` : "Gen9’s own"}</span>
          </p>
          <p className="mt-0.5 line-clamp-2 text-muted-foreground">{skill.description}</p>
        </li>
      ))}
    </ul>
  );
}
