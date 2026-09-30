"use client";

import { Menu01Icon, PencilEdit02Icon } from "@hugeicons/core-free-icons";
import { HugeiconsIcon } from "@hugeicons/react";
import Link from "next/link";
import { useState } from "react";

import { Mark } from "@/components/brand/logo";
import { buttonVariants } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { useStickyInset } from "@/lib/sticky-inset";

/** Phones and tablets: a top bar with the chat list in a drawer. */
export function MobileNav({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const bar = useStickyInset("top");

  return (
    <header ref={bar} className="sticky top-0 z-30 border-b bg-background/85 pt-safe backdrop-blur-md short:static lg:hidden">
      <div className="flex h-14 items-center gap-1 px-2 px-safe">
        <Sheet open={open} onOpenChange={setOpen}>
          <SheetTrigger className={buttonVariants({ variant: "ghost", size: "icon" })} aria-label="Open menu">
            <HugeiconsIcon icon={Menu01Icon} strokeWidth={1.8} className="size-5" />
          </SheetTrigger>
          <SheetContent side="left" className="w-[min(85vw,20rem)] bg-sidebar p-0" showCloseButton={false}>
            <SheetTitle className="sr-only">Menu</SheetTitle>
            <SheetDescription className="sr-only">Chats and account</SheetDescription>
            {/* Close the drawer once a link inside it is followed */}
            <div
              className="h-full pt-safe"
              onClick={(event) => {
                if ((event.target as HTMLElement).closest("a")) setOpen(false);
              }}
            >
              {children}
            </div>
          </SheetContent>
        </Sheet>
        <div className="flex min-w-0 flex-1 items-center justify-center gap-2">
          <Mark className="size-5" />
          <span className="truncate text-[0.9375rem] font-medium">{title}</span>
        </div>
        <Link href="/chat" className={buttonVariants({ variant: "ghost", size: "icon" })} aria-label="New chat">
          <HugeiconsIcon icon={PencilEdit02Icon} strokeWidth={1.8} className="size-5" />
        </Link>
      </div>
    </header>
  );
}
