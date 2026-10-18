"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { ROUTES } from "@/lib/routes";
import { useDemoState, useHydrated } from "@/lib/store/hooks";

/** Sends a signed-in user to the workspace (the public landing page is for visitors). */
export function RedirectIfSignedIn() {
  const router = useRouter();
  const hydrated = useHydrated();
  const authed = useDemoState().authed;
  useEffect(() => {
    if (hydrated && authed) router.replace(ROUTES.command);
  }, [hydrated, authed, router]);
  return null;
}
