"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { toast } from "sonner";

import { doneWords } from "@/lib/auth/account-actions";

/** After an account action on Keycloak (?updated=password, …), say what was done once and clean
 *  the URL. */
export function UpdatedToast() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const updated = params.getAll("updated").join(",");
  useEffect(() => {
    if (!updated) return;
    toast.success(doneWords(updated.split(",")));
    router.replace(pathname, { scroll: false });
  }, [updated, router, pathname]);
  return null;
}
