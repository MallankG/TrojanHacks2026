import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Horizontally scrollable table wrapper so wide tables never overflow the page. */
export function DataTable({ className, children, caption }: { className?: string; children: ReactNode; caption?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={cn("w-full border-collapse text-[13.5px]", className)}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        {children}
      </table>
    </div>
  );
}

export function Th({ className, ...props }: ComponentProps<"th">) {
  return <th scope="col" className={cn("whitespace-nowrap border-b border-line bg-[#fafbfc] px-3 py-2.5 text-left text-xs font-medium text-muted", className)} {...props} />;
}

export function Td({ className, ...props }: ComponentProps<"td">) {
  return <td className={cn("border-b border-line-soft px-3 py-2.5 align-middle", className)} {...props} />;
}

export function Tr({ selected, className, ...props }: ComponentProps<"tr"> & { selected?: boolean }) {
  return <tr className={cn(selected && "[&>td]:bg-[#eef7f5]", className)} {...props} />;
}
