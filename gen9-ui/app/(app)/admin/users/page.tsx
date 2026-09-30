import type { Metadata } from "next";

import { NeedsAdmin } from "@/components/admin/needs-admin";
import { UserActions } from "@/components/admin/user-actions";
import { LocalDate } from "@/components/local-date";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { orNotAdmin } from "@/lib/admin-access";
import { agentJson, type AdminUserPage } from "@/lib/agent";
import { requireSession } from "@/lib/auth/session";
import { initials } from "@/lib/initials";

export const metadata: Metadata = { title: "Users" };

export default async function UsersPage({ searchParams }: PageProps<"/admin/users">) {
  const session = await requireSession("/admin/users");
  if (!session.isAdmin) {
    return <NeedsAdmin />;
  }

  const params = await searchParams;
  const q = String(params.q ?? "").slice(0, 100);
  // Back from signing in again to delete someone: that person's dialog opens (UserActions)
  const reopen = typeof params.delete === "string" ? params.delete : null;
  const page = await orNotAdmin(
    agentJson<AdminUserPage>(session, `/v1/admin/users?max=100${q ? `&search=${encodeURIComponent(q)}` : ""}`),
  );
  if (!page) return <NeedsAdmin demoted />;

  return (
    <main className="mx-auto w-full max-w-4xl px-5 pt-8 pb-16 sm:px-8 lg:pt-14">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-headline font-semibold">Users</h1>
          <p className="mt-1 text-muted-foreground">
            {q
              ? `${page.total} ${page.total === 1 ? "person matches" : "people match"} “${q}”.`
              : `${page.total} ${page.total === 1 ? "person" : "people"} can sign in to Gen9.`}
          </p>
        </div>
        <form role="search" className="sm:w-72">
          <label htmlFor="user-search" className="sr-only">
            Search users
          </label>
          <Input id="user-search" name="q" defaultValue={q} placeholder="Search by name or email" type="search" />
        </form>
      </div>

      {page.users.length === 0 ? (
        <Empty className="mt-10 rounded-2xl border">
          <EmptyHeader>
            <EmptyTitle>No users match “{q}”</EmptyTitle>
            <EmptyDescription>Try part of a name or an email address.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <ul className="mt-8 divide-y rounded-2xl border bg-card">
          {page.users.map((user) => {
            const name = [user.first_name, user.last_name].filter(Boolean).join(" ") || null;
            const isSelf = user.id === session.user.sub;
            return (
              <li key={user.id} className="flex items-center gap-3 px-4 py-3.5 sm:gap-4 sm:px-5">
                <Avatar className="size-10">
                  <AvatarFallback className="bg-secondary text-xs font-semibold">{initials(name, user.email)}</AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium">
                    <span className="truncate">{name ?? user.email}</span>
                    {isSelf && <Badge variant="outline">You</Badge>}
                    {user.is_admin && <Badge>Admin</Badge>}
                    {!user.enabled && <Badge variant="destructive">Disabled</Badge>}
                    {user.locked && <Badge variant="destructive">Locked: too many sign-in attempts</Badge>}
                    {!user.email_verified && <Badge variant="secondary">Email not verified</Badge>}
                  </p>
                  <p className="truncate text-sm text-muted-foreground">
                    {user.email}
                    {user.created_at_ms ? (
                      <span className="hidden sm:inline">
                        , joined <LocalDate ms={user.created_at_ms} />
                      </span>
                    ) : null}
                  </p>
                </div>
                <UserActions user={user} isSelf={isSelf} deleting={!isSelf && reopen === user.id} />
              </li>
            );
          })}
        </ul>
      )}
    </main>
  );
}
