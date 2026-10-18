import type { Metadata } from "next";
import { ExportsView } from "@/features/governance/exports-view";

export const metadata: Metadata = { title: "Evidence exports · Governance" };

export default function GovernanceExportsPage() {
  return <ExportsView />;
}
