import type { Metadata } from "next";
import { CaseAccessGate } from "@/components/shell/access-restricted";
import { GraphScreen } from "@/features/graph/graph-screen";

export const metadata: Metadata = { title: "Graph workbench" };

export default function GraphPage() {
  return (
    <CaseAccessGate>
      <GraphScreen />
    </CaseAccessGate>
  );
}
