import { Audit01Icon, Calendar03Icon, PencilEdit02Icon, PuzzleIcon, Search01Icon, Settings01Icon, UserGroupIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";

import { Wordmark } from "@/components/brand/logo";
import { NavLink } from "@/components/app-shell/nav-link";
import { ThreadList } from "@/components/app-shell/thread-list";
import { UserMenu } from "@/components/app-shell/user-menu";
import { buttonVariants } from "@/components/ui/button";
import type { Thread } from "@/lib/agent";
import { cn } from "@/lib/utils";

export type ShellUser = { name: string | null; email: string | null; isAdmin: boolean };

export function SidebarContent({ user, threads }: { user: ShellUser; threads: Thread[] | null }) {
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-16 shrink-0 items-center px-5">
        <Link href="/chat" className="rounded-md" aria-label="Gen9, new chat">
          <Wordmark className="h-7" />
        </Link>
      </div>

      <div className="px-3">
        <Link href="/chat" className={cn(buttonVariants({ variant: "outline" }), "w-full justify-start")}>
          <HugeiconsIcon icon={PencilEdit02Icon} strokeWidth={2} data-icon="inline-start" />
          New chat
        </Link>
        <nav aria-label="Find" className="mt-1">
          <NavLink href="/search" icon={Search01Icon}>
            Search
          </NavLink>
          <NavLink href="/scheduled" icon={Calendar03Icon}>
            Scheduled
          </NavLink>
        </nav>
      </div>

      <nav aria-label="Chats" className="mt-6 min-h-0 flex-1 overflow-x-hidden overflow-y-auto px-3">
        <p className="px-3 pb-2 text-xs font-medium text-muted-foreground">Recent</p>
        <ThreadList threads={threads} />
      </nav>

      <nav aria-label="Account" className="grid gap-0.5 border-t px-3 py-3">
        <NavLink href="/settings" icon={Settings01Icon}>
          Settings
        </NavLink>
        {user.isAdmin && (
          <>
            <NavLink href="/admin/users" icon={UserGroupIcon}>
              Users
            </NavLink>
            <NavLink href="/admin/plugins" icon={PuzzleIcon}>
              Plugins
            </NavLink>
            <NavLink href="/admin/audit" icon={Audit01Icon}>
              Audit log
            </NavLink>
          </>
        )}
      </nav>
      <div className="border-t p-3 pb-safe [--safe-min:0.75rem]">
        <UserMenu user={user} />
      </div>
    </div>
  );
}
