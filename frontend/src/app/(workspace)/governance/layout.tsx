import type { ReactNode } from "react";
import { GovernanceHeader } from "@/features/governance/governance-header";

export default function GovernanceLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <GovernanceHeader />
      {children}
    </>
  );
}
