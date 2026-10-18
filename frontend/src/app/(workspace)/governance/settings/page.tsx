import type { Metadata } from "next";
import { PolicySettings } from "@/features/governance/policy-settings";
import { PrivacyControls } from "@/features/governance/privacy-controls";

export const metadata: Metadata = { title: "Policy and settings · Governance" };

export default function GovernanceSettingsPage() {
  return (
    <>
      <PrivacyControls />
      <PolicySettings />
    </>
  );
}
