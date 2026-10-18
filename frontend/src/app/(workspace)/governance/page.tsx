import type { Metadata } from "next";
import { LineageView } from "@/features/governance/lineage-view";

export const metadata: Metadata = { title: "Lineage · Governance" };

export default function GovernanceLineagePage() {
  return <LineageView />;
}
