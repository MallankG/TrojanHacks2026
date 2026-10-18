import type { Metadata } from "next";
import { CaseAccessGate } from "@/components/shell/access-restricted";
import { GraphWorkbench } from "@/features/graph/graph-workbench";

export const metadata: Metadata = { title: "Graph workbench" };

export default function GraphPage() {
  return (
    <CaseAccessGate>
      <GraphWorkbench />
    </CaseAccessGate>
  );
}
