import type { Metadata } from "next";
import { PipelinesView } from "@/features/governance/pipelines-view";

export const metadata: Metadata = { title: "Pipelines and agents · Governance" };

export default function GovernancePipelinesPage() {
  return <PipelinesView />;
}
