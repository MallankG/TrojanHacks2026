"use client";

import { closeModal, confirmCaseAction } from "@/lib/store/actions";
import type { ConfirmAction } from "@/lib/store/state";
import { useDemoState } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Dialog } from "@/components/ui/dialog";

const TITLES: Record<ConfirmAction, string> = {
  escalate: "Escalate CASE-0412 for SAR-support review?",
  close: "Close CASE-0412 with no further action?",
  more: "Request more information for CASE-0412?",
  approve: "Approve the escalation of CASE-0412?",
  return: "Return CASE-0412 to the analyst?",
};

export function ConfirmCaseDialog({ action }: { action: ConfirmAction }) {
  const rationale = useDemoState().caseWork.rationale;
  const isReview = action === "approve" || action === "return";
  return (
    <Dialog
      title={TITLES[action]}
      role="alertdialog"
      size="sm"
      hideClose
      onClose={closeModal}
      footer={
        <>
          <Button variant="ghost" onClick={closeModal}>
            Cancel
          </Button>
          <Button onClick={() => confirmCaseAction(action)}>Confirm</Button>
        </>
      }
    >
      <p className="text-[13.5px] text-muted">This is recorded in the audit trail under your name and cannot be edited. Nothing is filed, frozen or sent automatically.</p>
      {isReview ? <Callout tone="info">You are acting as the second reviewer.</Callout> : <Callout tone="mute" title="Rationale:">{rationale}</Callout>}
    </Dialog>
  );
}
