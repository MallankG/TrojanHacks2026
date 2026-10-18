"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, type ReactNode } from "react";
import { ROUTES, screenForPath } from "@/lib/routes";
import { markVisited } from "@/lib/store/actions";
import { useDemoState, useHydrated } from "@/lib/store/hooks";
import { Skeleton } from "@/components/ui/misc";
import { ModalHost } from "@/components/modals/modal-host";
import { Banners, CaseContextBar } from "./banners";
import { Sidebar } from "./sidebar";
import { Topbar } from "./topbar";

/** Authenticated workspace frame shared by every screen. */
export function AppShell({ children }: { children: ReactNode }) {
  const hydrated = useHydrated();
  const authed = useDemoState().authed;
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (hydrated && !authed) router.replace(ROUTES.signIn);
  }, [hydrated, authed, router]);

  useEffect(() => {
    if (hydrated && authed) markVisited(screenForPath(pathname));
  }, [hydrated, authed, pathname]);

  if (!hydrated || !authed) {
    return (
      <div className="flex min-h-screen items-center justify-center p-10">
        <div className="w-full max-w-md">
          <Skeleton label="Loading workspace" />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col lg:flex-row">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <Banners />
        <CaseContextBar />
        <main id="main" className="flex w-full max-w-[1320px] flex-col gap-5 px-7 pb-12 pt-6">
          {children}
        </main>
      </div>
      <ModalHost />
    </div>
  );
}
