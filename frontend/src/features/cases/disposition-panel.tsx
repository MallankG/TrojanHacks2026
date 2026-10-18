"use client";

import { useState } from "react";
import { DISPOSITION_OPTIONS, MIN_RATIONALE, isCaseVerified } from "@/lib/domain/case";
import { approvalBlocked, dispositionBlocked, openModal, setDisposition, setRationale, submitDisposition } from "@/lib/store/actions";
import { ROLES } from "@/lib/seed/roles";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Panel } from "@/components/ui/card";
import { OptionCard } from "@/components/ui/controls";
import { Field, TextArea } from "@/components/ui/field";
import { Pill } from "@/components/ui/pill";

/** A person decides. Escalation needs a verified narrative and a second reviewer. */
export function DispositionPanel() {
  const state = useDemoState();
  const role = useRole();
  const [error, setError] = useState<string | null>(null);
  const work = state.caseWork;
  const status = work.status;
  const verified = isCaseVerified(state);
  const lockedReason = dispositionBlocked(state);
  const canDecide = lockedReason === null;
  const pending = status === "Pending approval";
  const canApprove = pending && approvalBlocked(state) === null;
  const requester = work.escalatedBy ? ROLES[work.escalatedBy] : null;
  // An officer who requested the escalation needs a different second reviewer.
  const selfRequested = pending && role.canApproveEscalation && work.escalatedBy === role.id;

  return (
    <Panel className="border-2 border-ink">
      <h2 className="text-base font-semibold">Disposition</h2>
      {lockedReason ? <Callout tone="mute">{lockedReason}</Callout> : null}
      {pending && !canApprove ? (
        <Callout tone="warn" title={selfRequested ? "Waiting for a second reviewer." : "Waiting for a BSA officer."}>
          {selfRequested
            ? "You requested this escalation, so a different reviewer must approve it."
            : "Escalations need a second reviewer. Switch the Role menu to BSA officer to continue the demo."}
        </Callout>
      ) : null}
      {canApprove ? (
        <Callout
          tone="info"
          title={`Approval requested by ${requester ? requester.name : "the analyst"}.`}
          action={
            <>
              <Button size="sm" onClick={() => openModal({ kind: "confirm", action: "approve" })}>
                Approve escalation
              </Button>
              <Button variant="ghost" size="sm" onClick={() => openModal({ kind: "confirm", action: "return" })}>
                Return to analyst
              </Button>
            </>
          }
        >
          Rationale: {work.rationale}
        </Callout>
      ) : null}

      <div role="group" aria-label="Disposition options" className="flex flex-col gap-2">
        {DISPOSITION_OPTIONS.map((option) => {
          const locked = option.key === "escalate" && !verified;
          return (
            <OptionCard
              key={option.key}
              pressed={work.disposition === option.key}
              disabled={!canDecide}
              onClick={() => {
                setDisposition(option.key);
                setError(null);
              }}
            >
              <span className="flex flex-col gap-0.5">
                <b>{option.label}</b>
                <span className="text-xs text-muted">{option.description}</span>
              </span>
              {locked ? <Pill tone="warn">Locked until verified</Pill> : null}
            </OptionCard>
          );
        })}
      </div>

      <Field label="Rationale (saved in the audit trail)" error={error} hint={`At least ${MIN_RATIONALE} characters.`}>
        {(props) => (
          <TextArea
            {...props}
            rows={4}
            value={work.rationale}
            disabled={!canDecide}
            placeholder="What the evidence shows, what you weighed, why you decided"
            onChange={(event) => {
              setRationale(event.target.value);
              setError(null);
            }}
          />
        )}
      </Field>
      <div>
        <Button disabled={!canDecide} onClick={() => setError(submitDisposition())}>
          Record disposition
        </Button>
      </div>
      <span className="text-xs text-muted">No automatic SAR filing, account freeze, transaction rejection or customer contact.</span>
    </Panel>
  );
}
