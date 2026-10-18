"use client";

import { useState } from "react";
import { BLAST_RADIUS } from "@/lib/seed/data-ops";
import { applyMappingFix, closeModal } from "@/lib/store/actions";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, TextArea } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { CodeBlock } from "@/components/ui/misc";

export function MappingFixDialog() {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = () => setError(applyMappingFix(reason));

  return (
    <Dialog
      eyebrow="Mapping fix v4.1"
      title="Map bene_acct_no to target_account and reload?"
      onClose={closeModal}
      footer={
        <>
          <Button variant="ghost" onClick={closeModal}>
            Cancel
          </Button>
          <Button onClick={submit}>Approve, reload and re-score</Button>
        </>
      }
    >
      <CodeBlock>{'{ "contract": "ach.v4", "mapping": "v4.1", "target_account": "bene_acct_no" }'}</CodeBlock>
      <h3 className="text-[13.5px] font-semibold">Blast radius</h3>
      <KeyValueList items={BLAST_RADIUS} />
      <p className="text-xs text-muted">Source rows stay unchanged. Downstream outputs are re-computed and old versions are kept. You can roll back to v4.0.</p>
      <Field label="Reason (saved with the mapping version)" error={error}>
        {(props) => (
          <TextArea
            {...props}
            rows={3}
            value={reason}
            onChange={(event) => {
              setReason(event.target.value);
              setError(null);
            }}
            placeholder="Why this mapping is correct"
          />
        )}
      </Field>
    </Dialog>
  );
}
