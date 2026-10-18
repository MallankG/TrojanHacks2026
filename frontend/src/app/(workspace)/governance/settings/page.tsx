import type { Metadata } from "next";
import { PolicySettings } from "@/features/governance/policy-settings";

export const metadata: Metadata = { title: "Policy and settings · Governance" };

export default function GovernanceSettingsPage() {
  return <PolicySettings />;
}
