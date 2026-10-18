"use client";

import { ROUTES } from "@/lib/routes";
import { TabNav } from "@/components/ui/controls";
import { PageHeader } from "@/components/ui/page-header";

export function GovernanceHeader() {
  return (
    <>
      <PageHeader title="Governance, model and audit center" description="Every screen deepens the same case. This one proves how it was built." />
      <TabNav
        label="Governance sections"
        tabs={[
          { href: ROUTES.governance(), label: "Lineage" },
          { href: ROUTES.governance("models"), label: "Model registry" },
          { href: ROUTES.governance("drift"), label: "Drift and health" },
          { href: ROUTES.governance("pipelines"), label: "Pipelines and agents" },
          { href: ROUTES.governance("replay"), label: "Investigation replay" },
          { href: ROUTES.governance("sandbox"), label: "What-if sandbox" },
          { href: ROUTES.governance("audit"), label: "Access and audit" },
          { href: ROUTES.governance("exports"), label: "Evidence exports" },
          { href: ROUTES.governance("settings"), label: "Policy and settings" },
        ]}
      />
    </>
  );
}
