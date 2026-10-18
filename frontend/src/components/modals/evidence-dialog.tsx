"use client";

import { useRouter } from "next/navigation";
import { evidenceOrFallback } from "@/lib/domain/evidence";
import { ROUTES } from "@/lib/routes";
import { closeModal, updateGraph } from "@/lib/store/actions";
import { useEvidenceRegistry } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { EvidenceBody } from "@/features/evidence/evidence-body";

export function EvidenceDialog({ id }: { id: string }) {
  const router = useRouter();
  const item = evidenceOrFallback(useEvidenceRegistry(), id);
  const { edgeKey, accountKey, alertId } = item;

  const go = (href: string) => {
    closeModal();
    router.push(href);
  };

  return (
    <Dialog
      eyebrow={item.kind}
      title={item.title}
      onClose={closeModal}
      footer={
        <>
          <span />
          <div className="flex flex-wrap gap-2">
            {edgeKey ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  updateGraph({ selection: { kind: "edge", key: edgeKey } });
                  go(ROUTES.graph);
                }}
              >
                Show in graph
              </Button>
            ) : null}
            {accountKey ? (
              <Button variant="ghost" size="sm" onClick={() => go(ROUTES.entity(item.id))}>
                Open entity profile
              </Button>
            ) : null}
            {alertId ? (
              <Button variant="ghost" size="sm" onClick={() => go(ROUTES.riskAlert(alertId))}>
                Open alert
              </Button>
            ) : null}
            <Button variant="dark" size="sm" onClick={closeModal}>
              Done
            </Button>
          </div>
        </>
      }
    >
      <EvidenceBody item={item} originalLabel="Original source row" />
    </Dialog>
  );
}
