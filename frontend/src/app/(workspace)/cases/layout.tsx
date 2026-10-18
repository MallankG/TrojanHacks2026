import type { ReactNode } from "react";
import { CaseAccessGate } from "@/components/shell/access-restricted";
import { PageHeader } from "@/components/ui/page-header";

export default function CasesLayout({ children }: { children: ReactNode }) {
  return (
    <CaseAccessGate>
      <PageHeader title="Case management and report builder" description="A person decides. TraceLayer packages the evidence for human review; it never files with FinCEN." />
      {children}
    </CaseAccessGate>
  );
}
