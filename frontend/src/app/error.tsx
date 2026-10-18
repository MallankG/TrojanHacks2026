"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

export default function ErrorBoundary({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Log only the error type and digest: messages and stacks can contain case data.
    console.error("Screen error", error.name, error.digest ?? "");
  }, [error]);

  return (
    <main id="main" className="flex min-h-[60vh] items-center justify-center p-10">
      <div className="flex max-w-md flex-col gap-3 rounded-[14px] border border-line bg-white p-8">
        <div className="eyebrow">Something went wrong</div>
        <h1 className="text-2xl font-semibold">This screen could not load</h1>
        <p className="text-[13.5px] text-muted">
          Evidence and saved snapshots are unchanged.{error.digest ? ` Reference: ${error.digest}.` : ""}
        </p>
        <Button className="self-start" onClick={reset}>
          Try again
        </Button>
      </div>
    </main>
  );
}
