"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { Button } from "./button";

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface DialogProps {
  title: ReactNode;
  eyebrow?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  /** `alertdialog` for confirmations that interrupt the user. */
  role?: "dialog" | "alertdialog";
  /** Hide the header close button (the footer then owns dismissal). */
  hideClose?: boolean;
}

const SIZES = { sm: "max-w-[480px]", md: "max-w-[640px]", lg: "max-w-[860px]" } as const;

/** Accessible modal: traps focus, closes on Escape, restores focus on close. */
export function Dialog({ title, eyebrow, onClose, children, footer, size = "md", role = "dialog", hideClose }: DialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (nodes.length === 0) return;
      const firstNode = nodes[0];
      const lastNode = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === firstNode) {
        event.preventDefault();
        lastNode.focus();
      } else if (!event.shiftKey && document.activeElement === lastNode) {
        event.preventDefault();
        firstNode.focus();
      }
    };
    panel?.addEventListener("keydown", onKeyDown);
    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    return () => {
      panel?.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = overflow;
      previouslyFocused?.focus?.();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-[rgba(11,22,38,0.55)] px-5 py-11">
      <div ref={panelRef} role={role} aria-modal="true" aria-labelledby={titleId} tabIndex={-1} className={cn("w-full rounded-[18px] bg-white shadow-[0_24px_80px_rgba(11,22,38,0.35)] outline-none", SIZES[size])}>
        <div className="flex items-start justify-between gap-3 border-b border-line px-6 py-5">
          <div>
            {eyebrow ? <div className="eyebrow">{eyebrow}</div> : null}
            <h2 id={titleId} className="mt-1 text-xl font-semibold">
              {title}
            </h2>
          </div>
          {hideClose ? null : (
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
          )}
        </div>
        <div className="flex flex-col gap-3.5 px-6 py-5">{children}</div>
        {footer ? <div className="flex flex-wrap justify-between gap-2.5 border-t border-line px-6 py-3.5">{footer}</div> : null}
      </div>
    </div>
  );
}
