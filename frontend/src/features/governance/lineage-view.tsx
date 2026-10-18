"use client";

import { useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import { fieldImpact, lineageSteps, type ImpactField } from "@/lib/domain/governance";
import { useDemoState } from "@/lib/store/hooks";
import { Callout } from "@/components/ui/callout";
import { CardHeader, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { KeyValueList } from "@/components/ui/key-value";
import { Pill } from "@/components/ui/pill";

const FIELDS: ImpactField[] = ["target_account", "amount", "posted_at"];

/** Source file to reviewer decision for TX-88237, plus field-level downstream impact. */
export function LineageView() {
  const state = useDemoState();
  const steps = useMemo(() => lineageSteps(state), [state]);
  const [selected, setSelected] = useState(2);
  const [field, setField] = useState<ImpactField>("target_account");
  const step = steps[Math.min(selected, steps.length - 1)];
  const impact = fieldImpact(field, state.data.driftFixed);

  return (
    <>
      <Panel>
        <CardHeader title="From source file to reviewer decision · TX-88237 in CASE-0412" description="Eleven linked steps. A gap means the link does not exist yet; it is never invented." />
        <ol aria-label="Lineage steps" className="grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(150px,1fr))]">
          {steps.map((s, i) => (
            <li key={s.title}>
              <button
                type="button"
                aria-pressed={i === selected}
                onClick={() => setSelected(i)}
                className={cn(
                  "flex h-full w-full flex-col gap-1 rounded-xl border bg-white text-left",
                  i === selected ? "border-2 border-ink px-[11px] py-[9px]" : "border-line px-3 py-2.5 hover:border-[#9fb0c6]",
                )}
              >
                <span className="text-xs text-muted">Step {i + 1}</span>
                <b className="text-[13px]">{s.title}</b>
                <span className="break-all font-mono text-[11.5px]">{s.object}</span>
                <Pill tone={s.linked ? "ok" : "warn"} className="self-start">
                  {s.linked ? "Linked" : "Gap"}
                </Pill>
              </button>
            </li>
          ))}
        </ol>
        <div className="rounded-xl bg-subtle px-4 py-3.5">
          <div className="eyebrow">{step.title}</div>
          <KeyValueList
            className="mt-2"
            items={[
              ["Object", <span key="o" className="font-mono">{step.object}</span>],
              ["Version or detail", step.detail],
              ["Owner", step.owner],
              ["Time", step.at],
            ]}
          />
          {!step.linked ? (
            <Callout tone="warn" className="mt-2.5">
              Lineage gap: this step is not linked yet. Downstream outputs are marked stale until it is.
            </Callout>
          ) : null}
        </div>
      </Panel>
      <Panel>
        <CardHeader
          title="Field-level impact"
          description="What changes downstream if this source field is wrong or corrected."
          actions={
            <div role="group" aria-label="Source field" className="flex flex-wrap gap-1.5">
              {FIELDS.map((f) => (
                <Chip key={f} mono pressed={field === f} onClick={() => setField(f)}>
                  {f}
                </Chip>
              ))}
            </div>
          }
        />
        <ul className="flex flex-col">
          {impact.map((row) => (
            <li key={row.kind + row.value} className="flex flex-wrap justify-between gap-2.5 border-t border-line-soft py-2 text-[13.5px]">
              <span>
                <b>{row.kind}:</b> {row.value}
              </span>
              <Pill tone={row.tone}>{row.status}</Pill>
            </li>
          ))}
        </ul>
      </Panel>
    </>
  );
}
