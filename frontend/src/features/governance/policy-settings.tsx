"use client";

import { useState } from "react";
import { bandError } from "@/lib/domain/governance";
import { parseWeights, WEIGHT_KEYS, WEIGHT_LABELS, weightSum } from "@/lib/domain/priority";
import { FEATURE_FLAGS } from "@/lib/seed/governance";
import { PERMISSION_MATRIX } from "@/lib/seed/roles";
import { requestAiToggle, saveBand, saveWeights, toggleFlag } from "@/lib/store/actions";
import { useDemoState, useRole } from "@/lib/store/hooks";
import type { WeightKey } from "@/lib/store/state";
import { Button } from "@/components/ui/button";
import { Callout } from "@/components/ui/callout";
import { Card, CardHeader, Panel } from "@/components/ui/card";
import { Chip } from "@/components/ui/controls";
import { Field, TextInput } from "@/components/ui/field";
import { DataTable, Td, Th } from "@/components/ui/table";

const ROLE_COLUMNS = ["Analyst", "Data steward", "BSA officer", "Auditor"];

export function PolicySettings() {
  const state = useDemoState();
  const role = useRole();
  const policy = state.policy;
  const readOnly = !role.canEditPolicy;
  // Drafts stay local: nothing re-ranks or changes policy until a guarded save succeeds.
  const [bandDraft, setBandDraft] = useState({ min: policy.bandMin, max: policy.bandMax });
  const [weightsDraft, setWeightsDraft] = useState<Record<WeightKey, string>>({ ...policy.weights });
  const band = bandError(bandDraft.min, bandDraft.max);
  const weightsValid = parseWeights(weightsDraft) !== null;
  const total = weightSum(weightsDraft);
  const bandDirty = bandDraft.min !== policy.bandMin || bandDraft.max !== policy.bandMax;
  const weightsDirty = WEIGHT_KEYS.some((k) => weightsDraft[k] !== policy.weights[k]);

  return (
    <>
      {readOnly ? <Callout tone="mute">Read-only for your role. Only a BSA officer can change policy, weights, AI scope and feature flags.</Callout> : null}
      <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(340px,1fr))]">
        <Panel>
          <CardHeader title="Structuring-like review band" description="A configurable review parameter, not a legal threshold. Saved as a new rule version." />
          <div className="flex flex-wrap gap-3">
            <Field label="Band minimum ($)" error={band}>
              {(props) => <TextInput {...props} inputMode="numeric" value={bandDraft.min} disabled={readOnly} onChange={(event) => setBandDraft((d) => ({ ...d, min: event.target.value }))} />}
            </Field>
            <Field label="Band maximum ($)">
              {(props) => <TextInput {...props} inputMode="numeric" value={bandDraft.max} disabled={readOnly} onChange={(event) => setBandDraft((d) => ({ ...d, max: event.target.value }))} />}
            </Field>
          </div>
          <div>
            <Button size="sm" disabled={readOnly || band !== null || !bandDirty} onClick={() => saveBand(bandDraft.min, bandDraft.max)}>
              Save band
            </Button>
            {bandDirty && !readOnly ? <span className="ml-2 text-xs text-muted">Unsaved changes. Alerts use ${policy.bandMin} to ${policy.bandMax} until you save.</span> : null}
          </div>
        </Panel>
        <Panel>
          <CardHeader title={`Priority policy weights · ${policy.version}`} description="Must total 100. Saving creates a new policy version and re-ranks the queue." />
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {WEIGHT_KEYS.map((key) => (
              <Field key={key} label={<span className="text-xs">{WEIGHT_LABELS[key]}</span>}>
                {(props) => <TextInput {...props} inputMode="numeric" value={weightsDraft[key]} disabled={readOnly} onChange={(event) => setWeightsDraft((d) => ({ ...d, [key]: event.target.value }))} />}
              </Field>
            ))}
          </div>
          <span className="text-[13px]">
            Total: <b>{total}</b>
          </span>
          {!weightsValid ? (
            <span role="alert" className="text-[13px] font-semibold text-danger-ink">
              Weights must be whole numbers that add up to 100. The queue keeps using {policy.version} until they do.
            </span>
          ) : null}
          <div>
            <Button size="sm" disabled={readOnly || !weightsValid || !weightsDirty} onClick={() => saveWeights(weightsDraft)}>
              Save as new policy version
            </Button>
          </div>
        </Panel>
      </div>
      <div className="grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(340px,1fr))]">
        <Panel>
          <CardHeader title="AI scope" description="AI drafts text and answers questions only. Every prompt, tool call and output is logged." />
          <div className="flex items-center justify-between gap-2.5 border-t border-line-soft pt-2.5">
            <div>
              <b>Narrative drafting</b>
              <div className="text-xs text-muted">Kill switch for drafting on every case</div>
            </div>
            <Button variant="ghost" size="sm" aria-pressed={policy.aiDrafting} disabled={readOnly} onClick={requestAiToggle}>
              Drafting: {policy.aiDrafting ? "On" : "Off"}
            </Button>
          </div>
        </Panel>
        <Panel>
          <CardHeader title="Feature flags" description="Configuration, not authorization. Off by default when unproven." />
          <ul className="flex flex-col">
            {FEATURE_FLAGS.map((flag) => (
              <li key={flag.key} className="flex items-center justify-between gap-2.5 border-t border-line-soft py-2 text-[13.5px]">
                <span>{flag.label}</span>
                <Chip pressed={policy.flags[flag.key]} disabled={readOnly} aria-label={`${flag.label}: ${policy.flags[flag.key] ? "on" : "off"}`} onClick={() => toggleFlag(flag.key)}>
                  {policy.flags[flag.key] ? "On" : "Off"}
                </Chip>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
      <Card className="overflow-hidden">
        <div className="px-5 py-4">
          <CardHeader title="Roles and permissions" description="Enforced by row-level security and again by the API." />
        </div>
        <DataTable caption="Permissions by role">
          <thead>
            <tr>
              <Th>Action</Th>
              {ROLE_COLUMNS.map((c) => (
                <Th key={c}>{c}</Th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PERMISSION_MATRIX.map((row) => (
              <tr key={row.action}>
                <Td>{row.action}</Td>
                {row.values.map((value, i) => (
                  <Td key={ROLE_COLUMNS[i]}>{value}</Td>
                ))}
              </tr>
            ))}
          </tbody>
        </DataTable>
      </Card>
    </>
  );
}
