import type { Metadata } from "next";
import { LiveDataHealth } from "@/features/live/live-governance";
import { DriftHealth } from "@/features/governance/drift-health";

export const metadata: Metadata = { title: "Drift and health · Governance" };

export default function GovernanceDriftPage() {
  // Live backend panel (nothing without a live session), then the seeded tab content.
  return (
    <>
      <LiveDataHealth />
      <DriftHealth />
    </>
  );
}
