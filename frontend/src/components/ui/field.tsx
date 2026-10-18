import { useId, type ComponentProps, type ReactNode } from "react";
import { cn } from "@/lib/cn";

const INPUT = "min-h-11 w-full rounded-[10px] border border-[#b9c3d1] bg-white px-3 py-2.5 text-sm text-ink placeholder:text-[#7b8698] disabled:bg-subtle disabled:text-disabled-ink";

interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  error?: string | null;
  className?: string;
  children: (props: { id: string; "aria-describedby"?: string; "aria-invalid"?: boolean }) => ReactNode;
}

/** Labels a control and wires up hint and error text for assistive tech. */
export function Field({ label, hint, error, className, children }: FieldProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-[13.5px] font-semibold">
        {label}
      </label>
      {children({ id, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint ? (
        <span id={hintId} className="text-xs text-muted">
          {hint}
        </span>
      ) : null}
      {error ? (
        <span id={errorId} role="alert" className="text-[13px] font-semibold text-danger-ink">
          {error}
        </span>
      ) : null}
    </div>
  );
}

export function TextInput({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(INPUT, className)} {...props} />;
}

export function TextArea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(INPUT, "min-h-0 resize-y", className)} {...props} />;
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(INPUT, "pr-8", className)} {...props} />;
}

export function Checkbox({ label, className, ...props }: Omit<ComponentProps<"input">, "type"> & { label: ReactNode }) {
  return (
    <label className={cn("flex items-start gap-2.5 text-[13.5px]", className)}>
      <input type="checkbox" className="mt-0.5 size-[18px] shrink-0" {...props} />
      <span>{label}</span>
    </label>
  );
}
