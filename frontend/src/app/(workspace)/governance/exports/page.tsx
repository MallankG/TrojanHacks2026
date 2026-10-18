import type { Metadata } from "next";
import { LiveExports } from "@/features/live/live-governance";
import { ExportsView } from "@/features/governance/exports-view";

export const metadata: Metadata = { title: "Evidence exports · Governance" };

export default function GovernanceExportsPage() {
  // Live backend panel (nothing without a live session), then the seeded tab content.
  return (
    <>
      <LiveExports />
      <ExportsView />
    </>
  );
}
