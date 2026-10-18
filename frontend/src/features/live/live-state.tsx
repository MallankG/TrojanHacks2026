"use client";

import type { ReactNode } from "react";
import type { Loadable } from "@/lib/live/api";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Skeleton } from "@/components/ui/misc";

/** Renders loading and error states for a live query, then the children with data. */
export function LiveData<T>({ query, label, children }: { query: Loadable<T>; label: string; children: (data: T) => ReactNode }) {
  if (query.error) {
    return (
      <Callout
        tone="bad"
        role="alert"
        title={`Could not load ${label}.`}
        action={
          <Button variant="ghost" size="sm" onClick={query.reload}>
            Try again
          </Button>
        }
      >
        {query.error}
      </Callout>
    );
  }
  if (query.loading || query.data === null) return <Skeleton label={`Loading ${label}`} />;
  return <>{children(query.data)}</>;
}
