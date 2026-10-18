import type { Metadata } from "next";
import { SandboxView } from "@/features/governance/sandbox-view";

export const metadata: Metadata = { title: "What-if sandbox · Governance" };

export default function GovernanceSandboxPage() {
  return <SandboxView />;
}
