import type { Metadata } from "next";
import { LiveLineage } from "@/features/live/live-governance";
import { LineageView } from "@/features/governance/lineage-view";

export const metadata: Metadata = { title: "Lineage · Governance" };

export default function GovernanceLineagePage() {
  // Live backend panel (nothing without a live session), then the seeded tab content.
  return (
    <>
      <LiveLineage />
      <LineageView />
    </>
  );
}
