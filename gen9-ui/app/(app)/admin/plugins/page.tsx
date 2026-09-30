import type { Metadata } from "next";

import { NeedsAdmin } from "@/components/admin/needs-admin";
import { AddSource, PluginRow, SourceRow } from "@/components/admin/plugin-sources";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { orNotAdmin } from "@/lib/admin-access";
import { agentJson, type AdminPlugin, type PluginSource } from "@/lib/agent";
import { requireSession } from "@/lib/auth/session";

export const metadata: Metadata = { title: "Plugins" };

export default async function PluginsPage() {
  const session = await requireSession("/admin/plugins");
  if (!session.isAdmin) {
    return <NeedsAdmin />;
  }

  const loaded = await orNotAdmin(
    Promise.all([
      agentJson<PluginSource[]>(session, "/v1/admin/plugin-sources"),
      agentJson<AdminPlugin[]>(session, "/v1/admin/plugins"),
    ]),
  );
  if (!loaded) return <NeedsAdmin demoted />;
  const [sources, plugins] = loaded;

  return (
    <main className="mx-auto w-full max-w-4xl px-5 pt-8 pb-16 sm:px-8 lg:pt-14">
      <h1 className="text-headline font-semibold">Plugins</h1>
      <p className="mt-1 text-muted-foreground">Plugins come from git repositories you add. Nobody gets one until you make it available.</p>

      <section aria-labelledby="sources-title" className="mt-8">
        <h2 id="sources-title" className="text-title font-semibold">
          Sources
        </h2>
        <div className="mt-3 divide-y rounded-2xl border bg-card">
          {sources.length > 0 ? (
            <ul aria-label="Sources" className="divide-y">
              {sources.map((source) => (
                <SourceRow key={source.id} source={source} />
              ))}
            </ul>
          ) : (
            <Empty className="py-8">
              <EmptyHeader>
                <EmptyTitle>No plugin sources yet</EmptyTitle>
                <EmptyDescription>
                  Add a repository with <code>.agents/plugins/marketplace.json</code> or <code>.claude-plugin/marketplace.json</code>.
                </EmptyDescription>
              </EmptyHeader>
            </Empty>
          )}
          <AddSource />
        </div>
      </section>

      {sources.some((s) => plugins.some((p) => p.source_id === s.id)) && (
        <section aria-labelledby="plugins-title" className="mt-10">
          <h2 id="plugins-title" className="text-title font-semibold">
            Plugins
          </h2>
          {sources.map((source) => {
            const listed = plugins.filter((p) => p.source_id === source.id);
            if (!listed.length) return null;
            const name = source.name ?? source.url;
            return (
              <div key={source.id} className="mt-4">
                <h3 className="text-sm font-medium text-muted-foreground">{name}</h3>
                <ul aria-label={`Plugins from ${name}`} className="mt-2 divide-y rounded-2xl border bg-card">
                  {listed.map((plugin) => (
                    <PluginRow key={plugin.id} plugin={plugin} />
                  ))}
                </ul>
              </div>
            );
          })}
        </section>
      )}
    </main>
  );
}
