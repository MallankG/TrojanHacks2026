import type { ReactNode } from "react";
import { CaseAccessGate } from "@/components/shell/access-restricted";
import { RiskHeader } from "@/features/risk/risk-header";

export default function RiskLayout({ children }: { children: ReactNode }) {
  return (
    <CaseAccessGate>
      <RiskHeader />
      {children}
    </CaseAccessGate>
  );
}
