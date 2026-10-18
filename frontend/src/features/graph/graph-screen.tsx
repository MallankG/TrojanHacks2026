"use client";

import { Suspense } from "react";
import { useLiveProfile } from "@/lib/live/session";
import { Skeleton } from "@/components/ui/misc";
import { LiveGraphWorkbench } from "@/features/live/live-graph-workbench";
import { GraphWorkbench } from "./graph-workbench";

/**
 * Signed in to the backend: the live workbench on uploaded transactions. Otherwise the seeded
 * CASE-0412 workbench, unchanged. The live workbench reads `?account=` (and `?alert=`) from the URL,
 * so it sits under Suspense for prerendering.
 */
export function GraphScreen() {
  const profile = useLiveProfile();
  if (!profile) return <GraphWorkbench />;
  return (
    <Suspense fallback={<Skeleton label="Loading the graph workbench" />}>
      <LiveGraphWorkbench />
    </Suspense>
  );
}
