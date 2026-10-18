"use client";

import { useState } from "react";
import { ROUTES } from "@/lib/routes";
import { addNote } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { ButtonLink, Button } from "@/components/ui/button";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Field, TextArea } from "@/components/ui/field";
import { KeyValueList } from "@/components/ui/key-value";
import { EmptyState } from "@/components/ui/misc";
import { DataTable, Td, Th } from "@/components/ui/table";

const FACTS: Array<[string, string]> = [
  ["Typology", "Layering, rapid pass-through"],
  ["Amount entered path", "$58,200.00"],
  ["Accounts", "7 plus 1 device token"],
  ["Window", "Oct 6 to 9, 2026"],
  ["Owner", "M. Rivera"],
  ["Internal SLA", "Due Oct 11, 13:44 UTC (demo setting)"],
];

const CASE_OBJECTS = new Set(["CASE-0412", "CL-17"]);

export function CaseOverview() {
  const state = useDemoState();
  const role = useRole();
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const activity = state.events.filter((event) => CASE_OBJECTS.has(event.object)).slice(0, 8);

  const submit = () => {
    if (draft.trim().length < 3) {
      setError("Write a note of at least 3 characters.");
      return;
    }
    if (addNote(draft)) {
      setDraft("");
      setError(null);
    }
  };

  return (
    <>
      <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(340px,1fr))]">
        <Panel>
          <CardHeader title="Summary" />
          <KeyValueList items={FACTS} />
          <div className="flex flex-wrap gap-2">
            <ButtonLink href={ROUTES.graph} size="sm">
              Open graph
            </ButtonLink>
            <ButtonLink href={ROUTES.copilot} size="sm">
              Open copilot
            </ButtonLink>
            <ButtonLink href={ROUTES.entity("ACC-2208")} size="sm">
              Open profiles
            </ButtonLink>
          </div>
        </Panel>
        <Panel>
          <CardHeader title="Notes" description="Notes are saved to the case and the audit trail." />
          <ul className="flex flex-col">
            {state.caseWork.notes.map((note, index) => (
              <li key={`${note.at}-${index}`} className="border-t border-line-soft py-2 first:border-t-0 first:pt-0">
                <div className="text-xs text-muted">
                  {note.by} · {note.at}
                </div>
                <div className="text-[13.5px]">{note.text}</div>
              </li>
            ))}
          </ul>
          <Field label="Add a note" error={error} hint={role.readOnly ? "Auditors can read notes but not add them." : undefined}>
            {(props) => (
              <TextArea
                {...props}
                rows={2}
                value={draft}
                disabled={role.readOnly}
                placeholder="What you checked, what you found"
                onChange={(event) => {
                  setDraft(event.target.value);
                  setError(null);
                }}
              />
            )}
          </Field>
          <Button variant="ghost" size="sm" className="self-start" disabled={role.readOnly} onClick={submit}>
            Add note
          </Button>
        </Panel>
      </div>
      <Card className="overflow-hidden">
        <div className="px-5 py-4">
          <CardHeader title="Case activity" description="Newest first. Every event is also in the governance audit trail." />
        </div>
        {activity.length === 0 ? (
          <EmptyState title="No case activity yet." />
        ) : (
          <DataTable caption="Case activity">
            <thead>
              <tr>
                <Th>Time</Th>
                <Th>Actor</Th>
                <Th>Event</Th>
              </tr>
            </thead>
            <tbody>
              {activity.map((event) => (
                <tr key={event.requestId}>
                  <Td className="whitespace-nowrap text-xs text-muted">{event.at}</Td>
                  <Td>{event.actor}</Td>
                  <Td>{event.action}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </Card>
    </>
  );
}
