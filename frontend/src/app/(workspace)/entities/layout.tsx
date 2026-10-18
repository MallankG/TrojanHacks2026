import type { ReactNode } from "react";
import { CaseAccessGate } from "@/components/shell/access-restricted";

export default function EntitiesLayout({ children }: { children: ReactNode }) {
  return <CaseAccessGate>{children}</CaseAccessGate>;
}
