"use client";

import { useEffect } from "react";
import { dismissToast } from "@/lib/store/actions";
import { useDemoState } from "@/lib/store/hooks";

const TOAST_MS = 3800;

export function Toaster() {
  const toast = useDemoState().toast;

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => dismissToast(toast.id), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast]);

  return (
    <div aria-live="polite" className="pointer-events-none fixed bottom-6 left-1/2 z-[90] -translate-x-1/2">
      {toast ? (
        <div role="status" className="max-w-[90vw] rounded-xl bg-ink px-5 py-3 text-sm font-semibold text-white shadow-[0_12px_32px_rgba(11,22,38,0.35)]">
          {toast.message}
        </div>
      ) : null}
    </div>
  );
}
