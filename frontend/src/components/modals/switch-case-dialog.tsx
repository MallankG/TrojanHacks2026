"use client";

import { useState } from "react";
import { closeModal, switchCopilotCase } from "@/lib/store/actions";
import { useDemoState } from "@/lib/store/hooks";
import type { CaseId } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { OptionCard } from "@/components/ui/controls";
import { Dialog } from "@/components/ui/dialog";

const SWITCHABLE: CaseId[] = ["CASE-0412", "CASE-0398", "CASE-0405"];

export function SwitchCaseDialog() {
  const from = useDemoState().copilot.contextCase;
  const options = SWITCHABLE.filter((c) => c !== from);
  const [target, setTarget] = useState<CaseId>(options[0]);

  return (
    <Dialog
      title="Switch copilot context?"
      size="sm"
      onClose={closeModal}
      footer={
        <>
          <Button variant="ghost" onClick={closeModal}>
            Cancel
          </Button>
          <Button onClick={() => switchCopilotCase(target)}>Switch and clear chat</Button>
        </>
      }
    >
      <p className="text-[13.5px] text-muted">The chat for {from} will be cleared so evidence from one case never leaks into another. The transcript stays in the audit trail.</p>
      <div className="flex flex-col gap-2">
        {options.map((id) => (
          <OptionCard key={id} pressed={target === id} onClick={() => setTarget(id)}>
            <b className="font-mono">{id}</b>
          </OptionCard>
        ))}
      </div>
    </Dialog>
  );
}
