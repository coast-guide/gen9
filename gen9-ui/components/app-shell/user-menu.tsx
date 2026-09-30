"use client";

import { Audit01Icon, ComputerIcon, Logout01Icon, Moon02Icon, PuzzleIcon, Settings01Icon, Sun03Icon, UserGroupIcon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useRef } from "react";

import type { ShellUser } from "@/components/app-shell/sidebar";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { initials } from "@/lib/initials";
import { clearDrafts } from "@/lib/drafts";

export function UserMenu({ user }: { user: ShellUser }) {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const logoutForm = useRef<HTMLFormElement>(null);

  return (
    <>
      {/* Sign-out is a POST (not a link): prefetching or a third-party page can't sign you out */}
      <form ref={logoutForm} method="post" action="/auth/logout" className="hidden" />
      <DropdownMenu>
        <DropdownMenuTrigger className="flex w-full items-center gap-3 rounded-xl p-2 text-left transition-colors outline-hidden hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-ring data-[popup-open]:bg-sidebar-accent">
          <Avatar className="size-9">
            <AvatarFallback className="bg-foreground text-xs font-semibold text-background">
              {initials(user.name, user.email)}
            </AvatarFallback>
          </Avatar>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{user.name ?? user.email}</span>
            <span className="block truncate text-xs text-muted-foreground">{user.email}</span>
          </span>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="start" className="w-[var(--anchor-width)] min-w-60">
          <DropdownMenuGroup>
            <DropdownMenuLabel className="truncate">{user.email}</DropdownMenuLabel>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => router.push("/settings")}>
            <HugeiconsIcon icon={Settings01Icon} strokeWidth={1.8} />
            Settings
          </DropdownMenuItem>
          {user.isAdmin && (
            <>
              <DropdownMenuItem onClick={() => router.push("/admin/users")}>
                <HugeiconsIcon icon={UserGroupIcon} strokeWidth={1.8} />
                Users
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => router.push("/admin/plugins")}>
                <HugeiconsIcon icon={PuzzleIcon} strokeWidth={1.8} />
                Plugins
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => router.push("/admin/audit")}>
                <HugeiconsIcon icon={Audit01Icon} strokeWidth={1.8} />
                Audit log
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuLabel>Appearance</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={theme ?? "system"} onValueChange={(value) => setTheme(String(value))}>
              <DropdownMenuRadioItem value="system">
                <HugeiconsIcon icon={ComputerIcon} strokeWidth={1.8} />
                System
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="light">
                <HugeiconsIcon icon={Sun03Icon} strokeWidth={1.8} />
                Light
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="dark">
                <HugeiconsIcon icon={Moon02Icon} strokeWidth={1.8} />
                Dark
              </DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onClick={() => {
              // No one's unsent draft stays on the tab after sign-out (lib/drafts.ts)
              clearDrafts();
              logoutForm.current?.requestSubmit();
            }}
          >
            <HugeiconsIcon icon={Logout01Icon} strokeWidth={1.8} />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}
