import type { ReactNode } from "react";
import { DataHeader } from "@/features/data/data-header";

export default function DataLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <DataHeader />
      {children}
    </>
  );
}
