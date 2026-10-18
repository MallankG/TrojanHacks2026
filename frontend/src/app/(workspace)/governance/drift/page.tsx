import type { Metadata } from "next";
import { DriftHealth } from "@/features/governance/drift-health";

export const metadata: Metadata = { title: "Drift and health · Governance" };

export default function GovernanceDriftPage() {
  return <DriftHealth />;
}
