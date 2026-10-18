import type { ReactNode } from "react";
import { AppShell } from "@/components/shell/app-shell";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
