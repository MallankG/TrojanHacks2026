"use client";

import { ROUTES } from "@/lib/routes";
import { deniedMessage } from "@/lib/seed/roles";
import { openModal } from "@/lib/store/actions";
import { useLiveProfile } from "@/lib/live/session";
import { useRole } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { TabNav } from "@/components/ui/controls";
import { PageHeader } from "@/components/ui/page-header";

export function DataHeader() {
  const role = useRole();
  const live = useLiveProfile() !== null;
  const open = () => openModal({ kind: "onboard" });
  // With a live session, uploads go to the backend; onboarding a new source stays a guided mock.
  const upload = () => openModal(live ? { kind: "liveUpload" } : { kind: "onboard" });
  return (
    <>
      <PageHeader
        title="Data operations and governance"
        description="Who owns each feed, what passed the contract, and what a bad row changes downstream."
        actions={
          <div className="flex flex-col items-end gap-1.5">
            <div className="flex gap-2.5">
              <Button variant="ghost" disabled={!role.canUpload} onClick={open}>
                {live ? "Onboard a source (guided sample)" : "Onboard a source"}
              </Button>
              <Button disabled={!role.canUpload} onClick={upload}>
                Upload CSV batch
              </Button>
            </div>
            {!role.canUpload ? <span className="text-xs text-muted">{deniedMessage(role)}</span> : null}
          </div>
        }
      />
      <TabNav
        label="Data sections"
        tabs={[
          { href: ROUTES.data, label: "Sources and batches" },
          { href: ROUTES.dataQuality, label: "Quality exceptions" },
          { href: ROUTES.dataContracts, label: "Contracts and catalog" },
          { href: ROUTES.dataImpact, label: "Impact radar" },
        ]}
      />
    </>
  );
}
