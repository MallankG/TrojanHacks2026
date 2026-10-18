import type { Metadata } from "next";
import { LiveModelRegistry } from "@/features/live/live-governance";
import { ModelRegistry } from "@/features/governance/model-registry";

export const metadata: Metadata = { title: "Model registry · Governance" };

export default function GovernanceModelsPage() {
  // Live backend panel (nothing without a live session), then the seeded tab content.
  return (
    <>
      <LiveModelRegistry />
      <ModelRegistry />
    </>
  );
}
