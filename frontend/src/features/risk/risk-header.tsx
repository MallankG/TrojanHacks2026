"use client";

import { ROUTES } from "@/lib/routes";
import { TabNav } from "@/components/ui/controls";
import { PageHeader } from "@/components/ui/page-header";
import { RunDetectionButton } from "@/features/live/live-detection";

export function RiskHeader() {
  return (
    <>
      <PageHeader title="Risk intelligence" description="Every signal shown on its own. Priority ranks where to look first; it is never a finding about a person." actions={<RunDetectionButton />} />
      <TabNav
        label="Risk sections"
        tabs={[
          { href: ROUTES.riskAlert("AL-1027"), label: "Alert review", matchPrefix: "/risk/AL-" },
          { href: ROUTES.riskCompare, label: "Compare detectors" },
          { href: ROUTES.riskChanges, label: "Alert changes" },
        ]}
      />
    </>
  );
}
