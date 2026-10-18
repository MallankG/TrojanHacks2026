"use client";

import { useState } from "react";
import { BLOCKED_CLAIM_TEXT } from "@/lib/domain/case";
import { closeModal, saveClaimEdit } from "@/lib/store/actions";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, TextArea } from "@/components/ui/field";

export function EditClaimDialog() {
  const [text, setText] = useState(BLOCKED_CLAIM_TEXT);
  const [error, setError] = useState<string | null>(null);

  return (
    <Dialog
      title="Edit the blocked claim"
      onClose={closeModal}
      footer={
        <>
          <Button variant="ghost" onClick={closeModal}>
            Cancel
          </Button>
          <Button onClick={() => setError(saveClaimEdit(text))}>Re-verify and save</Button>
        </>
      }
    >
      <p className="text-[13.5px] text-muted">Your edit is re-verified before it is saved. Cite an evidence ID such as TX-88201 and use amounts that match it.</p>
      <Field label="Claim" error={error}>
        {(props) => (
          <TextArea
            {...props}
            rows={3}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setError(null);
            }}
          />
        )}
      </Field>
    </Dialog>
  );
}
