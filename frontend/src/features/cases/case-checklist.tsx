"use client";

import { useMemo } from "react";
import { buildChecklist } from "@/lib/domain/case";
import { percent } from "@/lib/format";
import { setCounterReviewed } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import { ButtonLink } from "@/components/ui/button";
import { CardHeader, Panel } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/field";
import { Meter } from "@/components/ui/misc";
import { Pill } from "@/components/ui/pill";

/** Investigation readiness: what still needs a person before a decision or export. */
export function CaseChecklist() {
  const state = useDemoState();
  const role = useRole();
  const items = useMemo(() => buildChecklist(state), [state]);
  const done = items.filter((item) => item.done).length;
  const ratio = done / items.length;

  return (
    <Panel>
      <CardHeader title="Investigation readiness" actions={<b>{percent(ratio)} ready</b>} />
      <Meter value={ratio} label="Investigation readiness" className="h-2.5 flex-none" />
      <ul className="flex flex-col">
        {items.map((item) => (
          <li key={item.label} className="flex flex-wrap items-center justify-between gap-2.5 border-t border-line-soft py-2.5">
            <div className="flex items-start gap-2.5">
              <Pill tone={item.done ? "ok" : "warn"}>{item.done ? "Done" : "Open"}</Pill>
              <div>
                <b className="text-[13.5px]">{item.label}</b>
                <div className="text-xs text-muted">{item.note}</div>
              </div>
            </div>
            {!item.done && item.href ? (
              <ButtonLink href={item.href} size="sm">
                Go
              </ButtonLink>
            ) : null}
          </li>
        ))}
      </ul>
      <Checkbox
        label="I reviewed the counter-evidence and benign explanations."
        checked={state.caseWork.counterReviewed}
        disabled={role.readOnly}
        onChange={(event) => setCounterReviewed(event.target.checked)}
      />
    </Panel>
  );
}
