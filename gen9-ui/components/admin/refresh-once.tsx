"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Renders the page again once, so its layout (the sidebar) reads the session as it is now. */
export function RefreshOnce() {
  const router = useRouter();
  useEffect(() => router.refresh(), [router]);
  return null;
}
