import type { Metadata } from "next";
import { ModelRegistry } from "@/features/governance/model-registry";

export const metadata: Metadata = { title: "Model registry · Governance" };

export default function GovernanceModelsPage() {
  return <ModelRegistry />;
}
