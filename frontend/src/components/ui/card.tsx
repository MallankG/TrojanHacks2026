import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Card({ className, ...props }: ComponentProps<"section">) {
  return <section className={cn("rounded-[14px] border border-line bg-white shadow-[0_1px_2px_rgba(14,26,43,0.04)]", className)} {...props} />;
}

interface CardHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
  className?: string;
  as?: "h2" | "h3";
}

export function CardHeader({ title, description, actions, eyebrow, className, as: Heading = "h2" }: CardHeaderProps) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        {eyebrow ? <div className="eyebrow mb-1">{eyebrow}</div> : null}
        <Heading className="text-base font-semibold">{title}</Heading>
        {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

/** Padded card with vertical rhythm, the most common layout. */
export function Panel({ className, ...props }: ComponentProps<"section">) {
  return <Card className={cn("flex flex-col gap-3 p-5", className)} {...props} />;
}
