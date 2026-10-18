import type { Metadata } from "next";
import { LivePipelines } from "@/features/live/live-governance";
import { PipelinesView } from "@/features/governance/pipelines-view";

export const metadata: Metadata = { title: "Pipelines and agents · Governance" };

export default function GovernancePipelinesPage() {
  // Live backend panel (nothing without a live session), then the seeded tab content.
  return (
    <>
      <LivePipelines />
      <PipelinesView />
    </>
  );
}
